"""HNSW and IVF-PQ builds measured against the exact oracle.

For every build and search setting the sweep records, per query, recall against the exact top
k of the same cohort; and per setting, queries per second, distances computed per query,
build time and an estimate of index bytes per vector.

Recall. Let m be the number of documents in the exact top k (fewer than k when the cohort is
smaller). Strict recall is the share of the exact top m found among the first m results.
Tie-aware recall counts a result as found when its exact score is at least the m-th exact
score, so a result tied with the exact list at its boundary is not a miss. Both are reported;
summaries use the tie-aware value. Queries whose cohort holds no document are excluded and
counted. Short results count the positions left empty within the first min(depth, eligible)
ranks, where the cohort could have filled them.

A build is defined by its family, parameters, insertion order and add batch: FAISS inserts
each add call's vectors level by level, so the same rows added in different batches give a
different graph. Multithreaded builds may not be bit-reproducible; each build records the
digest of its graph or inverted lists.
"""

from dataclasses import dataclass, field
from hashlib import sha256
from itertools import product
import json
from math import isfinite
from pathlib import Path
import tomllib

import numpy as np

from canonical import CanonicalRows
from oracle import exact_scores


QUANTILES = (0.0, 0.01, 0.05, 0.1, 0.5)
THRESHOLDS = (0.95, 0.99)


class SweepError(ValueError):
    """The sweep configuration is invalid or an index returned an ineligible document."""


# Configuration.

SECTIONS = {
    "inputs": {"space": str, "corpus": str, "transform": str, "queries": str},
    "oracle": {"k": int, "initial_candidates": int, "query_batch": int, "cohorts_permille": list},
    "measure": {"recall_at": list, "search_threads": int, "build_threads": int, "timing_repeats": int,
                "warmup": int, "add_batch": int},
}
FAMILIES = {
    "hnsw": {"name": str, "m": list, "ef_construction": list, "insertion_seeds": list, "ef_search": list,
             "cohorts_permille": list},
    "ivfpq": {"name": str, "nlist": list, "pq_m": list, "nbits": int, "train_size": int, "train_seed": int,
              "insertion_seeds": list, "nprobe": list, "cohorts_permille": list},
}


def _table(name: str, table, schema: dict) -> None:
    if not isinstance(table, dict) or set(table) != set(schema):
        raise SweepError(f"[{name}] needs exactly {sorted(schema)}")
    for key, kind in schema.items():
        value = table[key]
        if type(value) is not kind:
            raise SweepError(f"[{name}].{key} has the wrong type")
        if kind is list and (not value or any(type(item) is not int or item <= 0 for item in value)
                             or len(set(value)) != len(value)):
            raise SweepError(f"[{name}].{key} must list distinct positive integers")
        if kind is int and value < 0:
            raise SweepError(f"[{name}].{key} must not be negative")


@dataclass(frozen=True)
class Build:
    family: str
    name: str
    params: dict
    insertion_seed: int
    search: list = field(default_factory=list)      # ef_search or nprobe values
    cohorts: list = field(default_factory=list)

    @property
    def label(self) -> str:
        values = "-".join(f"{key}{value}" for key, value in sorted(self.params.items()))
        return f"{self.family}-{self.name}-{values}-s{self.insertion_seed}"


def parse_sweep(data: bytes) -> dict:
    try:
        config = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SweepError(f"Invalid sweep configuration: {error}") from error
    unknown = set(config) - set(SECTIONS) - set(FAMILIES)
    missing = set(SECTIONS) - set(config)
    if unknown or missing:
        raise SweepError(f"Unknown sections {sorted(unknown)}, missing {sorted(missing)}")
    for name, schema in SECTIONS.items():
        _table(name, config[name], schema)
    oracle, measure = config["oracle"], config["measure"]
    if any(value > 1000 for value in oracle["cohorts_permille"]):
        raise SweepError("Cohorts are permille values from 1 to 1000")
    depth = max(measure["recall_at"])
    if oracle["k"] < depth or oracle["initial_candidates"] < oracle["k"]:
        raise SweepError("The oracle depth must cover every recall cutoff, and candidates the oracle depth")
    if min(measure["timing_repeats"], measure["add_batch"], oracle["query_batch"]) < 1:
        raise SweepError("Repeats, add batch and query batch must be positive")
    names = set()
    for family, schema in FAMILIES.items():
        entries = config.get(family, [])
        if not isinstance(entries, list):
            raise SweepError(f"[[{family}]] must be an array of tables")
        for entry in entries:
            _table(family, entry, schema)
            if entry["name"] in names:
                raise SweepError(f"Duplicate sweep name {entry['name']}")
            names.add(entry["name"])
            if not set(entry["cohorts_permille"]) <= set(oracle["cohorts_permille"]):
                raise SweepError(f"{entry['name']}: every swept cohort needs an oracle")
            if family == "hnsw" and min(entry["ef_search"]) < depth:
                raise SweepError(f"{entry['name']}: ef_search below the search depth {depth} has no effect")
            if family == "ivfpq" and not 1 <= entry["nbits"] <= 16:
                raise SweepError(f"{entry['name']}: nbits must lie between 1 and 16")
    if not names:
        raise SweepError("The sweep defines no build")
    return config


def expand(config: dict) -> list[Build]:
    builds = []
    for entry in config.get("hnsw", []):
        for m, ef_construction, seed in product(entry["m"], entry["ef_construction"], entry["insertion_seeds"]):
            builds.append(Build("hnsw", entry["name"], {"m": m, "efc": ef_construction}, seed,
                                sorted(entry["ef_search"]), sorted(entry["cohorts_permille"], reverse=True)))
    for entry in config.get("ivfpq", []):
        for nlist, pq_m, seed in product(entry["nlist"], entry["pq_m"], entry["insertion_seeds"]):
            builds.append(Build("ivfpq", entry["name"],
                                {"nlist": nlist, "pqm": pq_m, "nbits": entry["nbits"],
                                 "train": entry["train_size"], "tseed": entry["train_seed"]},
                                seed, sorted(entry["nprobe"]), sorted(entry["cohorts_permille"], reverse=True)))
    labels = [build.label for build in builds]
    if len(set(labels)) != len(labels):
        raise SweepError("Two builds share a label")
    return builds


# Measurement, independent of FAISS.

def insertion_order(n: int, seed: int) -> np.ndarray:
    """The order in which rows are added: a permutation fixed by the seed."""
    return np.random.default_rng(seed).permutation(n).astype(np.int64)


def recall(found: np.ndarray, found_scores: np.ndarray, exact: np.ndarray, exact_scores_: np.ndarray, at: int):
    """Strict and tie-aware recall at `at` per query; NaN where the cohort holds no document.

    `found` are global rows returned by the index, -1 where it returned nothing, and
    `found_scores` their exact scores. `exact` and `exact_scores_` are the oracle arrays.
    """
    nq = exact.shape[0]
    strict, tie = np.full(nq, np.nan), np.full(nq, np.nan)
    for query in range(nq):
        reference = exact[query, :at]
        m = int((reference >= 0).sum())
        if m == 0:
            continue
        returned = found[query, :m]
        valid = returned >= 0
        strict[query] = np.isin(returned[valid], reference[:m]).sum() / m
        boundary = exact_scores_[query, m - 1]
        tie[query] = min(m, int((found_scores[query, :m][valid] >= boundary).sum())) / m
    return strict, tie


def summarize(values: np.ndarray) -> dict:
    present = values[~np.isnan(values)]
    if not present.size:
        return {"queries": 0, "excluded": int(values.size)}
    result = {"queries": int(present.size), "excluded": int(values.size - present.size),
              "mean": float(present.mean())}
    result.update({f"q{int(level * 100):02d}": float(np.quantile(present, level)) for level in QUANTILES})
    result.update({f"below_{threshold}": float((present < threshold).mean()) for threshold in THRESHOLDS})
    return result


def frontier(points: list[dict]) -> list[dict]:
    """Points not dominated in (recall, queries per second): nothing is at least as good on both and better on one."""
    keep = []
    for point in points:
        dominated = any(other["recall"] >= point["recall"] and other["qps"] >= point["qps"]
                        and (other["recall"] > point["recall"] or other["qps"] > point["qps"]) for other in points)
        if not dominated:
            keep.append(point)
    return sorted(keep, key=lambda point: (point["recall"], -point["qps"]))


def exact_scores_of(queries64: np.ndarray, documents: CanonicalRows, found: np.ndarray) -> np.ndarray:
    scores = np.full(found.shape, -np.inf, dtype=np.float64)
    for query in range(found.shape[0]):
        valid = found[query] >= 0
        if valid.any():
            scores[query, valid] = exact_scores(queries64[query], documents.take(found[query, valid]))
    return scores


# FAISS builds.

def set_threads(count: int) -> int:
    import faiss

    if count:
        faiss.omp_set_num_threads(count)
    return faiss.omp_get_max_threads()


def build_index(build: Build, documents: CanonicalRows, order: np.ndarray, add_batch: int):
    import faiss

    d = documents.dimensions
    if build.family == "hnsw":
        if documents.dtype == "float16":
            # Stores the canonical float16 rows exactly.
            index = faiss.IndexHNSWSQ(d, faiss.ScalarQuantizer.QT_fp16, build.params["m"], faiss.METRIC_INNER_PRODUCT)
            index.train(documents.take(order[:1], np.float32))
        else:
            index = faiss.IndexHNSWFlat(d, build.params["m"], faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = build.params["efc"]
    else:
        nlist, pq_m, nbits = build.params["nlist"], build.params["pqm"], build.params["nbits"]
        if d % pq_m:
            raise SweepError(f"{build.label}: the dimension {d} is not divisible by pq_m {pq_m}")
        if build.params["train"] > documents.n or build.params["train"] < max(nlist, 2 ** nbits):
            raise SweepError(f"{build.label}: the training sample must cover nlist and 2**nbits and fit the corpus")
        index = faiss.IndexIVFPQ(faiss.IndexFlatIP(d), d, nlist, pq_m, nbits, faiss.METRIC_INNER_PRODUCT)
        index.cp.seed = index.pq.cp.seed = build.params["tseed"]
        sample = np.sort(np.random.default_rng(build.params["tseed"]).choice(documents.n, build.params["train"],
                                                                            replace=False)).astype(np.int64)
        index.train(documents.take(sample, np.float32))
    for _, values in documents.chunks(order, add_batch):
        index.add(values)
    if index.ntotal != documents.n:
        raise SweepError(f"{build.label}: the index does not hold every row")
    return index


def _view(vector):
    import faiss

    return np.ascontiguousarray(faiss.rev_swig_ptr(vector.data(), vector.size())) if vector.size() else np.empty(0)


def structure_digest(build: Build, index) -> str:
    """Digest of the graph (HNSW) or of the inverted lists and centroids (IVF-PQ)."""
    import faiss

    digest = sha256()
    if build.family == "hnsw":
        for vector in (index.hnsw.neighbors, index.hnsw.offsets, index.hnsw.levels):
            digest.update(_view(vector).tobytes())
        return digest.hexdigest()
    lists = index.invlists
    for number in range(index.nlist):
        size = lists.list_size(number)
        digest.update(size.to_bytes(8, "little"))
        if size:
            digest.update(np.ascontiguousarray(faiss.rev_swig_ptr(lists.get_ids(number), size)).tobytes())
            digest.update(np.ascontiguousarray(faiss.rev_swig_ptr(lists.get_codes(number), size * index.code_size)).tobytes())
    digest.update(faiss.vector_to_array(index.pq.centroids).tobytes())
    digest.update(faiss.vector_to_array(faiss.downcast_index(index.quantizer).codes).tobytes())
    return digest.hexdigest()


def estimated_bytes(build: Build, index, documents: CanonicalRows) -> int:
    """Stored rows or codes plus graph or list overhead. Allocator overhead is not counted."""
    import faiss

    n, d = documents.n, documents.dimensions
    if build.family == "hnsw":
        storage = faiss.downcast_index(index.storage).code_size * n
        return int(storage + index.hnsw.neighbors.size() * 4 + index.hnsw.offsets.size() * 8 + index.hnsw.levels.size() * 4)
    return int(n * (index.code_size + 8) + index.nlist * d * 4 + index.pq.M * index.pq.ksub * index.pq.dsub * 4)


def selector(mask_internal: np.ndarray):
    """A FAISS bitmap selector over internal IDs. The bitmap must outlive every search."""
    import faiss

    bitmap = np.packbits(mask_internal, bitorder="little")
    return faiss.IDSelectorBitmap(mask_internal.size, faiss.swig_ptr(bitmap)), bitmap


def search_params(build: Build, value: int, chosen):
    import faiss

    if build.family == "hnsw":
        params = faiss.SearchParametersHNSW()
        params.efSearch = value
    else:
        params = faiss.SearchParametersIVF()
        params.nprobe = value
    if chosen is not None:
        params.sel = chosen
    return params


def reset_stats(build: Build) -> None:
    import faiss

    (faiss.cvar.hnsw_stats if build.family == "hnsw" else faiss.cvar.indexIVF_stats).reset()


def distances(build: Build) -> int:
    import faiss

    return int((faiss.cvar.hnsw_stats if build.family == "hnsw" else faiss.cvar.indexIVF_stats).ndis)


def timed_search(index, queries32, depth, params, build, *, repeats, warmup):
    from time import perf_counter

    for _ in range(warmup):
        index.search(queries32, depth, params=params)
    elapsed = []
    for repeat in range(repeats):
        if repeat == repeats - 1:
            reset_stats(build)
        started = perf_counter()
        _, found = index.search(queries32, depth, params=params)
        elapsed.append(perf_counter() - started)
    return found, elapsed, distances(build)


def json_bytes(value) -> bytes:
    if isinstance(value, float) and not isfinite(value):
        raise SweepError("Non-finite value in a sweep record")
    return (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def run_build(build: Build, documents: CanonicalRows, queries: CanonicalRows, oracles: dict, eligible_masks: dict,
              measure: dict, directory: Path) -> dict:
    """Build one index, sweep its search values over its cohorts, and write its records."""
    from time import perf_counter

    from vector_store import _save_atomic, _write_atomic

    directory.mkdir(parents=True, exist_ok=True)
    depth = max(measure["recall_at"])
    order = insertion_order(documents.n, build.insertion_seed)
    build_threads = set_threads(measure["build_threads"])
    started = perf_counter()
    index = build_index(build, documents, order, measure["add_batch"])
    build_seconds = perf_counter() - started
    search_threads = set_threads(measure["search_threads"])
    queries32 = queries.take(np.arange(queries.n), np.float32)
    queries64 = queries32.astype(np.float64)
    points, arrays = [], {}
    for permille in build.cohorts:
        exact_rows, exact_values = oracles[permille]
        mask = eligible_masks[permille]
        chosen, bitmap = (None, None) if mask.all() else selector(mask[order])
        for value in build.search:
            params = search_params(build, value, chosen)
            internal, elapsed, computed = timed_search(index, queries32, depth, params, build,
                                                       repeats=measure["timing_repeats"], warmup=measure["warmup"])
            found = np.where(internal >= 0, order[np.maximum(internal, 0)], -1)
            ineligible = int(((found >= 0) & ~mask[np.maximum(found, 0)]).sum())
            if ineligible:
                raise SweepError(f"{build.label}: {ineligible} ineligible results in cohort {permille}")
            found_scores = exact_scores_of(queries64, documents, found)
            point = {"build": build.label, "family": build.family, "cohort_permille": permille,
                     "search": {"ef_search" if build.family == "hnsw" else "nprobe": value},
                     # Empty positions the index could have filled: within the eligible count only.
                     "short_results": int((found[:, :min(depth, int(mask.sum()))] < 0).sum()), "seconds": elapsed,
                     "qps": queries.n / float(np.median(elapsed)), "distances_per_query": computed / queries.n,
                     "recall": {}}
            columns = []
            for at in measure["recall_at"]:
                strict, tie = recall(found, found_scores, exact_rows, exact_values, at)
                point["recall"][f"@{at}"] = {"tie_aware": summarize(tie), "strict": summarize(strict)}
                columns += [strict, tie]
            name = f"cohort{permille:04d}-{value:05d}.npy"
            values = np.ascontiguousarray(np.column_stack(columns), dtype="<f8")
            _save_atomic(directory / name, values)
            arrays[name] = sha256(values.tobytes()).hexdigest()
            point["per_query"] = {"file": name, "columns": [f"{kind}@{at}" for at in measure["recall_at"]
                                                            for kind in ("strict", "tie_aware")]}
            points.append(point)
        del chosen, bitmap
    size = estimated_bytes(build, index, documents)
    points_data = b"".join(json_bytes(point) for point in points)
    _write_atomic(directory / "points.jsonl", points_data)
    record = {"build": build.label, "family": build.family, "params": build.params,
              "insertion_seed": build.insertion_seed, "add_batch": measure["add_batch"],
              "build_seconds": build_seconds, "build_threads": build_threads, "search_threads": search_threads,
              "structure_sha256": structure_digest(build, index), "estimated_bytes": size,
              "estimated_bytes_per_vector": size / documents.n,
              "points_sha256": sha256(points_data).hexdigest(), "per_query_sha256": arrays}
    # Written last: a build directory without build.json is incomplete and is redone.
    _write_atomic(directory / "build.json", json_bytes(record))
    return record


def completed(directory: Path) -> dict | None:
    """The record of a finished build whose files still match it, else None."""
    try:
        record = json.loads((directory / "build.json").read_bytes())
        if sha256((directory / "points.jsonl").read_bytes()).hexdigest() != record["points_sha256"]:
            return None
        for name, expected in record["per_query_sha256"].items():
            if sha256(np.load(directory / name, allow_pickle=False).tobytes()).hexdigest() != expected:
                return None
        return record
    except (OSError, ValueError, KeyError):
        return None


def frontiers(points: list[dict], recall_at: list[int]) -> dict:
    """Pareto frontier of mean tie-aware recall against QPS, per cohort and cutoff, across builds."""
    result = {}
    for permille in sorted({point["cohort_permille"] for point in points}, reverse=True):
        for at in recall_at:
            candidates = [{"build": point["build"], "search": point["search"], "qps": point["qps"],
                           "recall": point["recall"][f"@{at}"]["tie_aware"].get("mean", 0.0),
                           "below_0.95": point["recall"][f"@{at}"]["tie_aware"].get("below_0.95")}
                          for point in points if point["cohort_permille"] == permille]
            result[f"cohort{permille:04d}@{at}"] = frontier(candidates)
    return result
