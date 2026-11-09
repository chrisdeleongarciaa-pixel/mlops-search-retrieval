"""Audit the paired SciFact artifact and recompute BM25 + HNSW fusion."""

import argparse
from collections import defaultdict
import gzip
from hashlib import sha256
import json
from math import fsum, isclose
from pathlib import Path

from paired_bootstrap import paired_interval, read_scores
from scifact_bm25 import digest
from scifact_dense import evaluate_rankings, summarize, trace_plan
from scifact_embeddings import load_split, write_json
from scifact_stability import PROTOCOL, aggregate


def recompute_rrf(lexical, dense):
    """Independent rank-only oracle for the registered two-branch fusion."""
    scores = defaultdict(list)
    for branch in (lexical, dense):
        ordered = sorted(branch, key=lambda doc: (-branch[doc], doc))
        for rank, doc in enumerate(ordered[:100], 1):
            scores[doc].append(1 / (60 + rank))
    sums = {doc: fsum(parts) for doc, parts in scores.items()}
    return dict(sorted(sums.items(), key=lambda item: (-item[1], item[0]))[:100])


def verify_samples(directory, manifest, queries, rankings):
    groups = {}
    for cut in map(str, PROTOCOL["cohorts_percent"]):
        with gzip.open(directory / f"samples-{cut}.jsonl.gz", "rt", encoding="utf-8") as source:
            rows = [json.loads(line) for line in source]
        if [row["query_id"] for row in rows] != list(queries):
            raise ValueError(f"Incomplete or reordered query coverage: {directory.name}/{cut}")
        for row in rows:
            eligible = manifest["cohorts"][cut]
            if (row["snapshot"] != manifest["generation"] or row["eligible"] != eligible["count"]
                    or row["eligible_ids_sha256"] != eligible["ids_sha256"]
                    or row["explore_additional_hits"] != PROTOCOL["explore_additional_hits"]
                    or row["total_target_hits"] != PROTOCOL["hits"]
                    or row["hits_requested"] != PROTOCOL["hits"]):
                raise ValueError(f"Sample differs from frozen profile: {directory.name}/{cut}")
            algorithm = trace_plan(row["trace"], True) if row["trace_ranking_matches"] else "indeterminate"
            if (algorithm != row["algorithm"] or trace_plan(row["exact_trace"], False) != "exact_requested"
                    or row["exact_algorithm"] != "exact_requested"
                    or not all(check["passed"] for check in row["exact_parity"].values())):
                raise ValueError(f"Trace or exact parity mismatch: {directory.name}/{cut}")
            if cut == "100":
                qid = row["query_id"]
                for k, field in ((10, "ann_recall_at_10"), (100, "ann_overlap_at_100")):
                    exact = list(rankings["exact"][qid])[:k]
                    approx = list(rankings["ann"][qid])[:k]
                    if not exact or not isclose(row[field], len(set(exact) & set(approx)) / len(exact)):
                        raise ValueError(f"ANN overlap mismatch: {directory.name}/{qid}")
        groups[cut] = summarize(rows)
    if groups != manifest["groups"]:
        raise ValueError(f"Recorded summaries differ from samples: {directory.name}")
    return groups


def run(artifact, dataset, evaluator, output):
    if digest(dataset) != PROTOCOL["dataset_sha256"]:
        raise ValueError("SciFact archive checksum mismatch")
    _, queries, qrels = load_split(dataset, "test")
    manifests, rankings, score_paths, builds = [], {}, {}, {}
    expected_files = {"freeze.json", "per-query.json", "rankings.json"} | {
        f"samples-{cut}.jsonl.gz" for cut in PROTOCOL["cohorts_percent"]
    }
    for manifest_path in sorted(artifact.glob("*/manifest.json")):
        folder = manifest_path.parent
        manifest = json.loads(manifest_path.read_bytes())
        if set(manifest["files_sha256"]) != expected_files:
            raise ValueError(f"Unexpected evidence set: {folder.name}")
        for name, checksum in manifest["files_sha256"].items():
            if digest(folder / name) != checksum:
                raise ValueError(f"Evidence checksum mismatch: {folder.name}/{name}")
        frozen = json.loads((folder / "freeze.json").read_bytes())
        if (any(manifest[key] != value for key, value in frozen.items())
                or manifest["freeze_sha256"] != digest(folder / "freeze.json")
                or manifest["query_ids_sha256"] != sha256("\n".join(queries).encode()).hexdigest()):
            raise ValueError(f"Freeze or query identity mismatch: {folder.name}")
        saved = json.loads((folder / "rankings.json").read_bytes())
        if set(saved) != {"bm25", "exact", "ann", "rrf_exact", "rrf_ann"}:
            raise ValueError(f"Unexpected ranking branches: {folder.name}")
        if any(branch.keys() != queries.keys() for branch in saved.values()):
            raise ValueError(f"Incomplete ranking query coverage: {folder.name}")
        for fusion, dense in (("rrf_exact", "exact"), ("rrf_ann", "ann")):
            recomputed = {qid: recompute_rrf(saved["bm25"][qid], saved[dense][qid]) for qid in queries}
            if any(list(recomputed[qid].items()) != list(saved[fusion][qid].items()) for qid in queries):
                raise ValueError(f"Fusion mismatch: {folder.name}/{fusion}")
            saved[fusion] = recomputed
        verify_samples(folder, manifest, queries, saved)
        manifests.append(manifest)
        score_paths[folder.name] = folder / "per-query.json"
        rankings.update({f"{folder.name}/{name}": branch for name, branch in saved.items()})
        builds[folder.name] = {
            "manifest_sha256": digest(manifest_path), "model": manifest["model"], "seed": manifest["seed"],
            "files_sha256": manifest["files_sha256"], "groups": manifest["groups"],
            "encoding_profile": manifest["encoding_profile"], "metrics": manifest["metrics"],
        }
    report = aggregate(manifests)
    original_report = artifact / "aggregate.json"
    if not report["paired_models"] or report != json.loads(original_report.read_bytes()):
        raise ValueError("Audit did not reproduce the paired aggregate")
    metrics, per_query = evaluate_rankings(rankings, qrels, evaluator)
    for build, path in score_paths.items():
        original_scores = json.loads(path.read_bytes())
        for ranker, scores in original_scores.items():
            key = f"{build}/{ranker}"
            if metrics[key] != builds[build]["metrics"][ranker] or per_query[key] != scores:
                raise ValueError(f"Pinned BEIR metrics differ: {key}")
    e5, gemma = ([score_paths[name] for name in builds if builds[name]["model"] == model]
                 for model in PROTOCOL["models"])
    def scores(paths, name):
        return read_scores(paths, name, "ndcg_cut_10")
    gemma_exact, gemma_ann = (scores(gemma, ranker) for ranker in ("exact", "ann"))
    intervals = {
        "gemma_exact_minus_e5_exact": paired_interval(gemma_exact, scores(e5, "exact")),
        "gemma_rrf_exact_minus_gemma_exact": paired_interval(scores(gemma, "rrf_exact"), gemma_exact),
        "gemma_rrf_ann_minus_gemma_ann": paired_interval(scores(gemma, "rrf_ann"), gemma_ann),
        "gemma_ann_minus_e5_ann": paired_interval(gemma_ann, scores(e5, "ann")),
    }
    output.mkdir()
    for build, path in score_paths.items():
        # Retain the original per-query bytes under deterministic compression.
        (output / f"{build}-per-query.json.gz").write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    write_json(output / "audit.json", {
        "dataset_sha256": digest(dataset), "evaluator_archive_sha256": digest(evaluator),
        "original_aggregate_sha256": digest(original_report), "audit_code_sha256": digest(Path(__file__)),
        "bootstrap_code_sha256": digest(Path(__file__).with_name("paired_bootstrap.py")),
        "verified_builds": builds, "aggregate": report, "paired_bootstrap_ndcg_at_10": intervals,
        "archived_scores_sha256": {file.name: digest(file) for file in sorted(output.glob("*.gz"))},
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("artifact", "dataset", "evaluator", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    run(args.artifact, args.dataset, args.evaluator, args.output)
