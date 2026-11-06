"""Freeze HNSW on SciFact train, then compare five test rankings on a pinned snapshot."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter_ns, sleep
from zipfile import ZipFile

import numpy as np

from search_retrieval.adapters.vespa import VespaWitness, _literal
from search_retrieval.query.eligible import Eligibility, SearchUnavailable
from search_retrieval.query.ranking import Ranked, fuse
from scifact_bm25 import EVALUATOR_ARCHIVE_SHA256, EVALUATOR_REVISION, EVALUATOR_SHA256, bm25, digest
from scifact_embeddings import ROOT, SPACE, encode_items, encoder, load_split, write_json
from vespa_lexical import percentiles


DEPTH = 100
TOLERANCE = 1e-5
EXPLORE = (0, 50, 100, 200)
TENANT = "scifact-dense"
ALGORITHMS = {
    "index top k": "hnsw", "index top k using filter": "hnsw",
    "exact fallback": "exact_by_selectivity", "exact": "exact_requested",
}


def trace_plan(trace, approximate):
    """Read the NN blueprint recorded by Vespa 8.781.11, or report unknown."""
    raw = json.dumps(trace).replace('\\"', '"')
    found = re.findall(
        r'\balgorithm["\s]*[:=]\s*"?(index top k using filter|index top k|exact fallback|exact)\b', raw,
    )
    effective = {ALGORITHMS[name] for name in found}
    if len(effective) != 1:
        return "indeterminate"
    algorithm = effective.pop()
    if (algorithm == "exact_requested") == approximate:
        return "indeterminate"
    return algorithm


def exact_runs(ids, documents, queries):
    """Independent FP64 cosine on the stored FP32 inputs, sorting ties by ID."""
    vectors = np.asarray(documents, dtype=np.float64)
    q = np.asarray(queries, dtype=np.float64)
    norms = np.linalg.norm(vectors, axis=1)
    q_norms = np.linalg.norm(q, axis=1)
    if (len(ids) != len(set(ids)) or len(ids) != len(vectors)
            or np.any(~np.isfinite(norms)) or np.any(norms == 0)
            or np.any(~np.isfinite(q_norms)) or np.any(q_norms == 0)):
        raise ValueError("Invalid or missing oracle vectors")
    if not ids:
        return [{} for _ in q]
    scores = (q / q_norms[:, None]) @ (vectors / norms[:, None]).T
    return [dict(sorted(zip(ids, map(float, row), strict=True), key=lambda hit: (-hit[1], hit[0]))[:DEPTH])
            for row in scores]


def overlap(reference, actual, k):
    expected = set(list(reference)[:k])
    return len(expected.intersection(list(actual)[:k])) / len(expected) if expected else float(not actual)


def parity(reference, actual, eligible_count, all_scores=None):
    """Separate cutoff exchanges within score tolerance from substantive changes."""
    scores = reference if all_scores is None else all_scores
    cuts = {}
    for k in (10, 100):
        expected = list(reference)[:k]
        got = list(actual)[:k]
        cutoff = reference[expected[-1]] if expected else 0
        lost = set(expected) - set(got)
        added = set(got) - set(expected)
        near_lost = sorted(doc for doc in lost if abs(scores[doc] - cutoff) <= TOLERANCE)
        near_added = sorted(doc for doc in added if doc in scores and abs(scores[doc] - cutoff) <= TOLERANCE)
        exchanges = min(len(near_lost), len(near_added))
        error = max((abs(score - scores[doc]) for doc, score in actual.items() if doc in scores), default=0.0)
        cuts[str(k)] = {
            "strict_overlap": overlap(reference, actual, k), "tie_exchanges": exchanges,
            "near_cutoff_lost": near_lost, "near_cutoff_added": near_added,
            "outside_tolerance": max(len(lost), len(added)) - exchanges,
            "max_score_error": error,
            "passed": len(got) == min(k, eligible_count) and len(lost) == len(added) == exchanges
            and error <= TOLERANCE,
        }
    return cuts


def read_vectors(directory, split, expected):
    manifest = json.loads((directory / f"{split}.json").read_text(encoding="utf-8"))
    filename = directory / f"{split}.npy"
    if manifest["vectors_sha256"] != digest(filename) or manifest["ids"] != sorted(expected):
        raise ValueError(f"{split}: vector bundle does not match the eligible snapshot")
    vectors = np.load(filename, allow_pickle=False)
    if (vectors.dtype != np.float32 or vectors.shape != (len(expected), 768)
            or not np.all(np.abs(np.linalg.norm(vectors, axis=1) - 1) <= TOLERANCE)):
        raise ValueError(f"{split}: incompatible or missing vectors")
    return manifest["ids"], vectors


def publish(index, snapshot, corpus, ids, vectors):
    def put(row):
        doc, vector = row
        index.feed(f"{snapshot.generation}-{doc}", {
            "tenant": TENANT, "generation": snapshot.generation, "projection_id": doc,
            "fragment_id": doc, "body": corpus[doc], "vector_space": SPACE.id,
            "digest": sha256(corpus[doc].encode()).hexdigest(),
            "embedding": {"values": vector.tolist()},
        })

    with ThreadPoolExecutor(max_workers=8) as workers:
        for _ in workers.map(put, zip(ids, vectors, strict=True)):
            pass
    filters = index._filter(TENANT, snapshot.generation)
    deadline = monotonic() + 60
    while monotonic() < deadline:
        response = index._search_response(
            f"select projection_id from fragment where {filters} and vector_space contains {_literal(SPACE.id)}",
            0, 5, ranking="unranked",
        )
        if response["root"]["fields"]["totalCount"] == len(ids):
            return
        sleep(.2)
    raise SearchUnavailable("Incomplete SciFact generation")


def run_query(index, snapshot, vector, *, approximate, explore, trace=False, whole=False):
    response = index.dense_response(snapshot, vector, SPACE.id, 5, approximate=approximate,
                                    explore=explore, trace=trace, whole_generation=whole, include_body=False)
    root = response["root"]
    candidates = [(row["fields"]["projection_id"], float(row["relevance"])) for row in root.get("children", [])]
    ranking = dict(sorted(candidates, key=lambda item: (-item[1], item[0])))
    if (len(ranking) != len(candidates) or not ranking.keys() <= snapshot.projections
            or len(ranking) != min(DEPTH, len(snapshot.projections))
            or any(not isfinite(score) for score in ranking.values())):
        raise SearchUnavailable("Unexpected vector ranking coverage or eligibility")
    return ranking, response.get("trace"), root.get("fields", {}).get("totalCount")


def evaluate_rankings(rankings, qrels, archive):
    import pytrec_eval

    if digest(archive) != EVALUATOR_ARCHIVE_SHA256:
        raise ValueError("BEIR evaluator archive checksum mismatch")
    with TemporaryDirectory() as directory, ZipFile(archive) as source:
        root = Path(directory).resolve()
        if any(not (root / name).resolve().is_relative_to(root) for name in source.namelist()):
            raise ValueError("Invalid evaluator archive path")
        source.extractall(root)
        repo = root / f"beir-{EVALUATOR_REVISION}"
        module = repo / "beir/retrieval/evaluation.py"
        if digest(module) != EVALUATOR_SHA256:
            raise ValueError("BEIR evaluator source checksum mismatch")
        sys.path.insert(0, str(repo))
        from beir.retrieval.evaluation import EvaluateRetrieval
        if Path(sys.modules[EvaluateRetrieval.__module__].__file__).resolve() != module:
            raise ValueError("Unexpected BEIR evaluator import")
        metrics = {}
        per_query = {}
        for name, results in rankings.items():
            ordered = {qid: {doc: 1 / rank for rank, doc in enumerate(hits, 1)} for qid, hits in results.items()}
            ndcg, _, recall, _ = EvaluateRetrieval.evaluate(qrels, ordered, [10, 100], ignore_identical_ids=True)
            scores = pytrec_eval.RelevanceEvaluator(qrels, {"ndcg_cut.10", "recall.10,100"}).evaluate(ordered)
            if scores.keys() != qrels.keys():
                raise ValueError("Evaluation did not score all 300 queries")
            per_query[name] = scores
            metrics[name] = {"NDCG@10": ndcg["NDCG@10"], "Recall@10": recall["Recall@10"], "Recall@100": recall["Recall@100"]}
        return metrics, per_query


def matrix_scores(ids, documents, vector):
    a = np.asarray(documents, dtype=np.float64)
    q = np.asarray(vector, dtype=np.float64)
    return dict(zip(ids, map(float, (a @ q) / (np.linalg.norm(a, axis=1) * np.linalg.norm(q))), strict=True))


def measure_cohort(index, snapshot, docs, queries, query_vectors, explore, cohort):
    qids = list(queries)
    ids = sorted(snapshot.projections)
    oracle = exact_runs(ids, docs, query_vectors)
    whole = cohort == "all"
    rows, exact, approximate = [], {}, {}
    for qid, vector, expected in zip(qids, query_vectors.tolist(), oracle, strict=True):
        vespa_exact, exact_trace, exact_count = run_query(index, snapshot, vector, approximate=False, explore=0, trace=True, whole=whole)
        score_map = matrix_scores(ids, docs, vector)
        comparisons = parity(expected, vespa_exact, len(ids), score_map)
        start = perf_counter_ns()
        ann, _, _ = run_query(index, snapshot, vector, approximate=True, explore=explore, whole=whole)
        duration = (perf_counter_ns() - start) / 1_000_000
        traced, trace, matches = run_query(index, snapshot, vector, approximate=True, explore=explore, trace=True, whole=whole)
        algorithm = trace_plan(trace, True) if traced == ann else "indeterminate"
        rows.append({
            "query_id": qid, "snapshot": snapshot.generation, "cohort": cohort, "eligible": len(ids),
            "explore_additional_hits": explore, "total_target_hits": DEPTH, "hits_requested": DEPTH,
            "returned_depth": len(ann), "vespa_total_count": matches,
            "exact_vespa_total_count": exact_count, "trace_ranking_matches": traced == ann,
            "engine_client_ms": duration, "algorithm": algorithm, "trace": trace,
            "exact_algorithm": trace_plan(exact_trace, False), "exact_trace": exact_trace,
            "exact_vespa_ranking": vespa_exact, "exact_parity": comparisons,
            "ann_recall_at_10": overlap(expected, ann, 10), "ann_overlap_at_100": overlap(expected, ann, 100),
        })
        exact[qid], approximate[qid] = expected, ann
    return rows, exact, approximate


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault(f"{row['cohort']}/{row['algorithm']}", []).append(row)
    return {
        name: {
            "queries": len(samples),
            "ann_recall_at_10": sum(row["ann_recall_at_10"] for row in samples) / len(samples),
            "ann_overlap_at_100": sum(row["ann_overlap_at_100"] for row in samples) / len(samples),
            "depth_range": [min(row["returned_depth"] for row in samples), max(row["returned_depth"] for row in samples)],
            "engine_client_ms": percentiles([row["engine_client_ms"] for row in samples]),
        } for name, samples in groups.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "dataset", "embeddings", "evaluator", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--vespa", default=os.environ.get("WITNESS_VESPA_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    args.output.mkdir()
    env = json.loads((args.embeddings / "environment.json").read_text(encoding="utf-8"))
    if env["space_id"] != SPACE.id or not json.loads((args.embeddings / "conformance.json").read_text())["passed"]:
        raise ValueError("Use a verified E5 vector bundle")
    corpus, train, _ = load_split(args.dataset, "train")
    ids, documents = read_vectors(args.embeddings, "corpus", corpus)
    _, train_vectors = read_vectors(args.embeddings, "train", train)
    generation = "scifact-" + digest(args.embeddings / "corpus.npy")
    snapshot = Eligibility(TENANT, generation, frozenset(ids))
    index = VespaWitness(args.vespa)
    publish(index, snapshot, corpus, ids, documents)
    train_exact = exact_runs(ids, documents, train_vectors)
    tuning = {}
    for explore in EXPLORE:
        samples = []
        for vector, expected in zip(train_vectors.tolist(), train_exact, strict=True):
            ann, _, _ = run_query(index, snapshot, vector, approximate=True, explore=explore, whole=True)
            samples.append(overlap(expected, ann, 10))
        tuning[explore] = sum(samples) / len(samples)
        print(f"train explore={explore}: recall@10={tuning[explore]:.6f}", flush=True)
    explore = next((value for value in EXPLORE if tuning[value] >= .95), max(EXPLORE, key=tuning.get))
    freeze = {
        "generation": generation, "train_queries": len(train), "train_recall_ann_at_10": tuning,
        "explore_additional_hits": explore, "total_target_hits": DEPTH, "hits": DEPTH,
        "selection_rule": "first ascending exploration with train recall >= 0.95, else greatest train recall",
        "workload": "single client sequential; no tracing on measured call; precomputed query vectors",
        "rank_profile": "dense", "schema_sha256": digest(ROOT / "infra/vespa/app/schemas/fragment.sd"),
        "vespa_compose_sha256": digest(ROOT / "infra/vespa/compose.witness.yaml"),
        "vector_space": SPACE.id, "dataset_sha256": env["dataset_sha256"],
    }
    write_json(args.output / "freeze.json", freeze)

    _, test, qrels = load_split(args.dataset, "test")
    adapter = encoder(args.model, args.device, args.threads)
    test_info = encode_items(adapter, test, "query", args.batch_size, args.output / "test.npy")
    write_json(args.output / "test.json", test_info)
    vectors = np.load(args.output / "test.npy", allow_pickle=False)
    rows, exact, ann = measure_cohort(index, snapshot, documents, test, vectors, explore, "all")
    # Stable subsamples of the same vector generation isolate prefilter selectivity.
    bucket = [int(sha256(doc.encode()).hexdigest(), 16) % 100 for doc in ids]
    for threshold in (10, 1):
        selected = [idx for idx, value in enumerate(bucket) if value < threshold]
        filtered = Eligibility(TENANT, generation, frozenset(ids[idx] for idx in selected))
        subset = dict(list(test.items())[:30])
        samples, _, _ = measure_cohort(index, filtered, documents[selected], subset, vectors[:len(subset)], explore, f"hash_lt_{threshold}")
        rows.extend(samples)
    lexical = bm25(corpus, test)
    rankings = {"bm25": lexical, "exact": exact, "hnsw_requested": ann}
    for name, branch in (("rrf_exact", exact), ("rrf_hnsw_requested", ann)):
        rankings[name] = {
            qid: {hit.id: hit.score for hit in fuse(
                [Ranked(doc, score) for doc, score in lexical[qid].items()],
                [Ranked(doc, score) for doc, score in branch[qid].items()],
            ).fused[:DEPTH]}
            for qid in test
        }
    metrics, per_query = evaluate_rankings(rankings, qrels, args.evaluator)
    groups = summarize(rows)
    hnsw = groups.get("all/hnsw")
    manifest = {
        "freeze_sha256": digest(args.output / "freeze.json"), "test_queries": len(test),
        "original_split": "SciFact dev", "groups": groups, "metrics": metrics,
        "exact_parity_passed": all(cut["passed"] for row in rows for cut in row["exact_parity"].values()),
        "RET-01": {
            "scope": "unfiltered HNSW pilot, 300 queries, one sequential client",
            "passed": None if hnsw is None or hnsw["queries"] != len(test) else
            hnsw["ann_recall_at_10"] >= .95 and hnsw["engine_client_ms"]["p95"] < 25,
        },
        "RET-02": {name: values["NDCG@10"] >= .75 for name, values in metrics.items()},
        "evaluator_revision": EVALUATOR_REVISION, "evaluator_sha256": EVALUATOR_SHA256,
    }
    for name, value in (("samples", rows), ("rankings", rankings), ("per-query", per_query)):
        path = args.output / f"{name}.json"
        write_json(path, value)
        manifest[f"{name}_sha256"] = digest(path)
    write_json(args.output / "manifest.json", manifest)
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
