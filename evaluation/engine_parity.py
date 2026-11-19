"""Step 5 gate: the engine's exact and approximate dense search against the exact oracle.

    python evaluation/engine_parity.py CONFIG --collection LABEL --vectors-dir DIR --out-dir DIR \
        --projections-dir DIR --vespa-url URL [--exact-sample 500] [--explore 0 100 300]

OUT_DIR holds the oracles written by `ann.py oracle`; the report is written beside them.
Exact search in the engine reads every eligible vector per query: on MS MARCO, keep the
sample small (about 100 queries).

Exact parity. For a fixed sample of queries chosen by ID hash, `approximate:false` in each
cohort must return the oracle's top k up to ties: the sorted score lists agree within 1e-5
position by position, common documents agree within 1e-5, and a document in only one list
scores within 1e-5 of the k-th oracle score.

Approximate search. Every query, every cohort and every `hnsw.exploreAdditionalHits` value is
measured with the recall definitions of the FAISS sweeps, with the strategy the engine
reports in its trace. Timings are single-client observations, not the service measurement.
"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from time import perf_counter

import numpy as np

from search_retrieval.adapters.vespa_passages import VespaPassages
from search_retrieval.experiment.manifests import ManifestError, inside, parse_manifest
from search_retrieval.experiment.provenance import ProvenanceError, _git, verify_clean, verify_repository
from search_retrieval.experiment.run_config import ConfigError
from search_retrieval.query.eligible import SearchUnavailable
from ann import open_sets, oracle_directory
from ann_sweep import exact_scores_of, parse_sweep, recall, summarize
from embed import ROOT
from oracle import OracleError, read_oracle
from scifact_dense import trace_plan, trace_text
from vector_store import _save_atomic, _write_atomic, in_parity_sample


TOLERANCE = 1e-5
FILTER_FIRST = re.compile(r"filter[ _-]?first|acorn", re.IGNORECASE)


def exact_agreement(engine_rows: list[int], engine_scores: list[float], oracle_rows: np.ndarray,
                    oracle_scores: np.ndarray, exact_of_engine: np.ndarray) -> dict:
    """Compare one query's exact engine list with the oracle, allowing differences only at ties."""
    m = int((oracle_rows >= 0).sum())
    expected_rows, expected_scores = oracle_rows[:m], oracle_scores[:m]
    if len(engine_rows) != m:
        return {"agrees": False, "reason": f"{len(engine_rows)} results, oracle has {m}"}
    gaps = np.abs(np.asarray(engine_scores) - expected_scores)
    by_row = dict(zip(expected_rows.tolist(), expected_scores.tolist()))
    common = [abs(score - by_row[row]) for row, score in zip(engine_rows, engine_scores) if row in by_row]
    boundary = expected_scores[-1] if m else 0.0
    only_engine = [exact for row, exact in zip(engine_rows, exact_of_engine) if row not in by_row]
    only_oracle = [score for row, score in by_row.items() if row not in set(engine_rows)]
    tied = all(abs(score - boundary) <= TOLERANCE for score in only_engine + only_oracle)
    agrees = bool((gaps <= TOLERANCE).all() and all(gap <= TOLERANCE for gap in common) and tied)
    return {"agrees": agrees, "max_score_gap": float(gaps.max()) if m else 0.0,
            "differing": len(only_engine), "reason": None if agrees else "scores or members beyond ties"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--vectors-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--projections-dir", type=Path, required=True)
    parser.add_argument("--vespa-url", required=True)
    parser.add_argument("--exact-sample", type=int, default=500)
    parser.add_argument("--explore", type=int, nargs="+", default=[0, 100, 300])
    args = parser.parse_args()
    try:
        verify_repository(ROOT)
        verify_clean(ROOT)
        config = parse_sweep(inside(ROOT, args.config).read_bytes())
        documents, queries = open_sets(config, args.vectors_dir)
        projection, _ = parse_manifest((args.projections_dir / args.collection / "manifest.json").read_bytes(),
                                       "vespa-projection")
        if projection["vector_set"] != documents.digest:
            raise ManifestError("The collection projects another vector set")
        k = max(config["measure"]["recall_at"])
        client = VespaPassages(args.vespa_url, timeout=120)
        position = {item: index for index, item in enumerate(documents.ids)}
        vectors = queries.take(np.arange(queries.n), np.float32)
        vectors64 = vectors.astype(np.float64)
        every = max(1, queries.n // args.exact_sample)
        sample = [index for index, item in enumerate(queries.ids) if in_parity_sample(item, every)]
        directory = args.out_dir / f"{documents.digest[:16]}-{queries.digest[:16]}" / f"engine-{args.collection}"
        directory.mkdir(parents=True, exist_ok=True)
        report = {"collection": args.collection, "documents": documents.digest, "queries": queries.digest,
                  "code_commit": _git(ROOT, "rev-parse", "HEAD"), "k": k, "tolerance": TOLERANCE,
                  "exact": {}, "approximate": {}}
        for permille in sorted(config["oracle"]["cohorts_permille"], reverse=True):
            oracle_body, oracle_rows, oracle_scores = read_oracle(oracle_directory(args.out_dir, documents, queries, permille))
            below = permille if permille < 1000 else None
            outcomes = []
            for index in sample:
                hits, _ = client.dense(args.collection, vectors[index].tolist(), k, approximate=False, cohort_below=below)
                rows = [position[hit.doc_id] for hit in hits]
                exact = exact_scores_of(vectors64[index:index + 1], documents, np.array([rows + [-1] * (k - len(rows))]))[0]
                outcomes.append(exact_agreement(rows, [hit.score for hit in hits], oracle_rows[index, :k],
                                                oracle_scores[index, :k], exact[:len(rows)]))
            report["exact"][permille] = {
                "queries": len(outcomes), "agree": sum(item["agrees"] for item in outcomes),
                "max_score_gap": max((item.get("max_score_gap", 0.0) for item in outcomes), default=0.0),
                "failures": [queries.ids[index] for index, item in zip(sample, outcomes) if not item["agrees"]][:20],
                "oracle": oracle_body["rows_sha256"],
            }
            print(json.dumps({"cohort_permille": permille, "exact": report["exact"][permille]}), flush=True)
            for explore in args.explore:
                found = np.full((queries.n, k), -1, dtype=np.int64)
                strategies, filter_first, elapsed = Counter(), 0, []
                for index in range(queries.n):
                    started = perf_counter()
                    hits, payload = client.dense(args.collection, vectors[index].tolist(), k, approximate=True,
                                                 explore=explore, cohort_below=below, trace=True)
                    elapsed.append(perf_counter() - started)
                    trace = payload.get("trace", {})
                    strategies[trace_plan(trace, True)] += 1
                    filter_first += bool(FILTER_FIRST.search(trace_text(trace)))
                    rows = [position[hit.doc_id] for hit in hits]
                    found[index, :len(rows)] = rows
                found_scores = exact_scores_of(vectors64, documents, found)
                columns, summary = [], {}
                for at in config["measure"]["recall_at"]:
                    strict, tie = recall(found, found_scores, oracle_rows, oracle_scores, at)
                    summary[f"@{at}"] = {"tie_aware": summarize(tie), "strict": summarize(strict)}
                    columns += [strict, tie]
                name = f"cohort{permille:04d}-explore{explore:04d}.npy"
                values = np.ascontiguousarray(np.column_stack(columns), dtype="<f8")
                _save_atomic(directory / name, values)
                key = f"{permille}:{explore}"
                report["approximate"][key] = {
                    "cohort_permille": permille, "explore_additional_hits": explore, "recall": summary,
                    "strategies": dict(strategies), "traces_mentioning_filter_first": filter_first,
                    "short_results": int((found[:, :min(k, int(oracle_body["cohort"]["eligible"]))] < 0).sum()),
                    "single_client_ms": {"p50": float(np.median(elapsed) * 1000),
                                         "p95": float(np.quantile(elapsed, 0.95) * 1000)},
                    "per_query": {"file": name, "sha256": sha256(values.tobytes()).hexdigest()},
                }
                print(json.dumps({key: report["approximate"][key]["recall"][f"@{min(config['measure']['recall_at'])}"]
                                  ["tie_aware"], "strategies": dict(strategies)}), flush=True)
        _write_atomic(directory / "report.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
        passed = all(item["agree"] == item["queries"] for item in report["exact"].values())
        print(json.dumps({"report": str(directory / "report.json"), "exact_parity": passed}))
        return 0 if passed else 2
    except (ConfigError, ManifestError, OracleError, ProvenanceError, SearchUnavailable, ValueError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
