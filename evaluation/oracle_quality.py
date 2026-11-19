"""Retrieval quality of a space from its certified exact oracle, without an engine.

    python evaluation/oracle_quality.py ORACLE_COHORT_DIR --query-vectors-manifest FILE \
        --queries MANIFEST --data-dir DIR [--baseline-per-query FILE] [--out FILE]

The oracle of the full cohort holds, for every query, the exact top 100 of the space by float64
score with ties by document ID (rows.npy: row positions, scores.npy). Positions are document
IDs when the vector set's IDs are "0", "1", ... in order, as for MS MARCO passage: that is
checked against the set's ids_sha256 by the caller (`--ids-are-positions` asserts it was). The
query order is the query vector set's, checked here against its ids_sha256. Judgments come from
ir_datasets. Reported: RR@10, nDCG@10 and R@100 (the oracle's depth), and, with
`--baseline-per-query` (a workbench per-query.jsonl), the paired 95 % interval of RR@10 minus
the baseline's.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import numpy as np

from search_retrieval.experiment.manifests import read_manifest
from search_retrieval.experiment.textmode import rerun_in_utf8_mode
from embed import ROOT
from measures import aggregate, evaluate
from paired_bootstrap import paired_interval


MEASURES = ("RR@10", "nDCG@10", "R@100")


def rankings_from_oracle(rows: np.ndarray, scores: np.ndarray, query_ids: list[str]) -> dict:
    if rows.shape != scores.shape or rows.shape[0] != len(query_ids):
        raise ValueError("The oracle arrays do not match the queries")
    return {qid: [(str(int(doc)), float(score)) for doc, score in zip(rows[index], scores[index]) if doc >= 0]
            for index, qid in enumerate(query_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("oracle", type=Path, help="an oracle cohort directory with rows.npy and scores.npy")
    parser.add_argument("--query-vectors-manifest", type=Path, required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ids-are-positions", action="store_true", required=True,
                        help="the document vector set's IDs are its row positions (checked by hash beforehand)")
    parser.add_argument("--baseline-per-query", type=Path)
    parser.add_argument("--baseline-name", default="baseline")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rerun = rerun_in_utf8_mode()
    if rerun is not None:
        return rerun
    query_set, _ = read_manifest(ROOT, args.queries, "query-set")
    os.environ["IR_DATASETS_HOME"] = str((args.data_dir / "ir_datasets").resolve())
    import ir_datasets

    dataset = ir_datasets.load(query_set["ir_datasets"])
    query_ids = sorted(query.query_id for query in dataset.queries_iter())
    vectors = json.loads(args.query_vectors_manifest.read_text(encoding="utf-8"))
    body = vectors.get("body", vectors)
    if sha256("".join(f"{qid}\n" for qid in query_ids).encode()).hexdigest() != body["ids_sha256"]:
        print("The query order differs from the query vector set", file=sys.stderr)
        return 1
    judgments: dict[str, dict[str, int]] = {}
    for row in dataset.qrels_iter():
        judgments.setdefault(row.query_id, {})[row.doc_id] = int(row.relevance)
    rankings = rankings_from_oracle(np.load(args.oracle / "rows.npy"), np.load(args.oracle / "scores.npy"), query_ids)
    per_query = evaluate(rankings, judgments, MEASURES)
    report = {"queries": len(query_ids), "depth": int(max(len(hits) for hits in rankings.values())),
              "metrics": aggregate(per_query), "oracle": str(args.oracle.name)}
    if args.baseline_per_query:
        baseline = {row["qid"]: row["metrics"]["RR@10"] for row in map(json.loads, args.baseline_per_query.read_text(
            encoding="utf-8").splitlines()) if row}
        dense = {qid: row["RR@10"] for qid, row in per_query.items()}
        report["paired_RR@10"] = {"against": args.baseline_name, **paired_interval(dense, baseline)}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
