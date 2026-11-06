"""Measure and aggregate SciFact across fresh HNSW graphs and separate vector spaces."""

import argparse
from collections import Counter
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
from statistics import mean, stdev

import numpy as np

from search_retrieval.adapters.vespa import VespaWitness
from search_retrieval.query.eligible import Eligibility
from scifact_bm25 import bm25, digest
from scifact_dense import (
    DEPTH, SPACE, TENANT, cosine_matrix, evaluate_rankings, hybrid_rankings,
    measure_cohort, overlap, publish, read_vectors, run_query, summarize, top_exact,
)
from scifact_embeddings import ROOT, load_split, write_json


PROTOCOL_PATH = ROOT / "config/scifact-stability-v1.json"
PROTOCOL = json.loads(PROTOCOL_PATH.read_bytes())
ORIGINAL_E5_AUDIT = ROOT / "evaluation/evidence/scifact-dense-actions-42275419629/audit.json"


def ordered_ids(ids, seed):
    """Use a recorded permutation, independent of Python's PRNG version."""
    return sorted(ids, key=lambda doc: (sha256(f"{seed}:{doc}".encode()).digest(), doc))


def bucket(doc):
    return int(sha256(doc.encode()).hexdigest(), 16) % 100


def count_documents(index, predicate):
    root = index._search_response(
        f"select projection_id from fragment where {predicate}", 0, 10, ranking="unranked",
    )["root"]
    return root["fields"]["totalCount"]


def read_test(path, queries, space):
    metadata = json.loads((path / "test.json").read_bytes())
    if "space_id" in metadata:
        if metadata["space_id"] != space or metadata.get("protocol_sha256") != digest(PROTOCOL_PATH):
            raise ValueError("Test vectors belong to another space or comparison protocol")
    else:
        # The original E5 run recorded its test space and hash in separate artifacts.
        frozen = json.loads((path / "freeze.json").read_bytes())
        manifest = json.loads((path / "manifest.json").read_bytes())
        audit = json.loads(ORIGINAL_E5_AUDIT.read_bytes())["test"]
        if (space != SPACE.id or frozen["vector_space"] != space
                or manifest["freeze_sha256"] != digest(path / "freeze.json")
                or audit["metadata_sha256"] != digest(path / "test.json")
                or audit["vectors_sha256"] != metadata["vectors_sha256"]):
            raise ValueError("Original E5 test vectors have incompatible provenance")
    return read_vectors(path, "test", queries)[1]


def validate_bundle(directory):
    env = json.loads((directory / "environment.json").read_bytes())
    conform = json.loads((directory / "conformance.json").read_bytes())
    if (env["model"] not in PROTOCOL["models"] or env["dataset_sha256"] != PROTOCOL["dataset_sha256"]
            or env["inference_precision"] != PROTOCOL["inference_precision"]
            or env["stored_dtype"] != PROTOCOL["stored_dtype"] or env["max_tokens"] != 512
            or conform.get("passed") is not True):
        raise ValueError("The embedding bundle does not meet the comparison protocol")
    if env["model"] == PROTOCOL["models"][0] and env["space_id"] != SPACE.id:
        raise ValueError("Unexpected E5 space")
    if env["model"] == PROTOCOL["models"][1]:
        from scifact_gemma import SPACE as GEMMA_SPACE
        if env["space_id"] != GEMMA_SPACE.id:
            raise ValueError("Unexpected EmbeddingGemma space")
    return env


def run(args):
    env = validate_bundle(args.embeddings)
    space = env["space_id"]
    corpus, train, _ = load_split(args.dataset, "train")
    ids, documents = read_vectors(args.embeddings, "corpus", corpus)
    _, train_vectors = read_vectors(args.embeddings, "train", train)
    index = VespaWitness(args.vespa)
    if count_documents(index, "true") != 0:
        raise ValueError("Use a newly deployed, empty Vespa index for each insertion order")
    insertion = ordered_ids(ids, args.seed)
    offsets = {doc: i for i, doc in enumerate(ids)}
    generation = f"study-{space[:16]}-{digest(args.embeddings / 'corpus.npy')[:16]}-{args.seed}"
    snapshot = Eligibility(TENANT, generation, frozenset(ids))
    buckets = {doc: bucket(doc) for doc in ids}
    publish(index, snapshot, corpus, insertion, documents[[offsets[doc] for doc in insertion]],
            space=space, buckets=buckets)
    if count_documents(index, "true") != len(ids):
        raise ValueError("The graph must contain exactly the study corpus")

    eligible = {cut: [i for i, doc in enumerate(ids) if buckets[doc] < cut]
                for cut in PROTOCOL["cohorts_percent"]}
    for cut, selected in eligible.items():
        if count_documents(index, f"study_bucket < {cut}") != len(selected):
            raise ValueError("Vespa and the cohort oracle disagree")

    explore = PROTOCOL["explore_additional_hits"]
    train_scores = cosine_matrix(ids, documents, train_vectors)
    train_recall = []
    for vector, scores in zip(train_vectors.tolist(), train_scores, strict=True):
        ann, _, _ = run_query(index, snapshot, vector, approximate=True, explore=explore, whole=True, space=space)
        train_recall.append(overlap(top_exact(dict(zip(ids, map(float, scores), strict=True))), ann, 10))
    args.output.mkdir()
    frozen = {
        "protocol_sha256": digest(PROTOCOL_PATH), "model": env["model"], "space": space,
        "seed": args.seed, "insertion_ids_sha256": sha256("\n".join(insertion).encode()).hexdigest(),
        "generation": generation, "corpus_vectors_sha256": digest(args.embeddings / "corpus.npy"),
        "train_vectors_sha256": digest(args.embeddings / "train.npy"),
        "embedding_environment_sha256": digest(args.embeddings / "environment.json"),
        "encoding_profile": {
            "device": env["device"], "device_name": env["device_name"],
            "torch": env["packages"]["torch"], "transformers": env["packages"]["transformers"],
        },
        "engine_host": {
            "identity_sha256": sha256(platform.node().encode()).hexdigest(),
            "machine": platform.machine(), "logical_cpus": os.cpu_count(),
        },
        "schema_sha256": digest(ROOT / "infra/vespa/app/schemas/fragment.sd"),
        "vespa_compose_sha256": digest(ROOT / "infra/vespa/compose.witness.yaml"),
        "explore_additional_hits": explore, "train_queries": len(train), "train_recall_ann_at_10": mean(train_recall),
        "cohorts": {
            str(cut): {"count": len(selected), "fraction": len(selected) / len(ids),
                       "ids_sha256": sha256("\n".join(ids[i] for i in selected).encode()).hexdigest()}
            for cut, selected in eligible.items()
        },
    }
    write_json(args.output / "freeze.json", frozen)
    _, queries, qrels = load_split(args.dataset, "test")
    vectors = read_test(args.test_vectors, queries, space)
    if digest(args.test_vectors / "test.npy") == frozen["train_vectors_sha256"]:
        raise ValueError("Test and train must be separate vector bundles")
    lexical = bm25(corpus, queries)
    groups = {}
    exact_parity = True
    for cut, selected in eligible.items():
        cohort = Eligibility(TENANT, generation, frozenset(ids[i] for i in selected))
        rows, exact, ann = measure_cohort(
            index, cohort, documents[selected], queries, vectors, explore,
            "all" if cut == 100 else f"lt_{cut}", space=space, bucket=None if cut == 100 else cut,
        )
        exact_parity &= all(row["exact_algorithm"] == "exact_requested" and
                            all(check["passed"] for check in row["exact_parity"].values()) for row in rows)
        groups[str(cut)] = summarize(rows)
        with gzip.open(args.output / f"samples-{cut}.jsonl.gz", "wt", encoding="utf-8") as sink:
            for row in rows:
                sink.write(json.dumps(row, allow_nan=False) + "\n")
        if cut == 100:
            rankings = {"bm25": lexical, "exact": exact, "ann": ann,
                        "rrf_exact": hybrid_rankings(lexical, exact), "rrf_ann": hybrid_rankings(lexical, ann)}
            metrics, per_query = evaluate_rankings(rankings, qrels, args.evaluator)
            write_json(args.output / "rankings.json", rankings)
            write_json(args.output / "per-query.json", per_query)
        print(f"{env['model']} seed={args.seed} cohort={cut} completed {len(rows)} comparisons", flush=True)
    summary = {
        **frozen, "freeze_sha256": digest(args.output / "freeze.json"), "queries_per_cohort": len(queries),
        "query_ids_sha256": sha256("\n".join(queries).encode()).hexdigest(),
        "test_vectors_sha256": digest(args.test_vectors / "test.npy"), "exact_parity_passed": exact_parity,
        "metrics": metrics, "groups": groups, "observations": len(queries) * len(eligible),
    }
    summary["files_sha256"] = {file.name: digest(file) for file in sorted(args.output.iterdir())}
    write_json(args.output / "manifest.json", summary)


def aggregate(manifests):
    expected_seeds = set(PROTOCOL["insertion_order_seeds"])
    by_model = {}
    for item in manifests:
        by_model.setdefault(item["model"], []).append(item)
    if not by_model or not set(by_model) <= set(PROTOCOL["models"]):
        raise ValueError("Unexpected models in stability report")
    for items in by_model.values():
        if len(items) != len(expected_seeds) or {row["seed"] for row in items} != expected_seeds:
            raise ValueError("Require one independent build for each registered insertion order")
        if len({row["insertion_ids_sha256"] for row in items}) != len(expected_seeds):
            raise ValueError("Insertion orders must be distinct")
        for field in ("space", "test_vectors_sha256", "corpus_vectors_sha256", "train_vectors_sha256"):
            if len({row[field] for row in items}) != 1:
                raise ValueError(f"Incompatible runs within one model: {field}")
        if any(row["engine_host"] != items[0]["engine_host"] or row["encoding_profile"] != items[0]["encoding_profile"]
               for row in items):
            raise ValueError("All builds for one encoder require the same recorded host and encoding profile")
    first = manifests[0]
    for row in manifests:
        if (row["protocol_sha256"] != digest(PROTOCOL_PATH) or row["cohorts"] != first["cohorts"]
                or row["schema_sha256"] != first["schema_sha256"] or row["vespa_compose_sha256"] != first["vespa_compose_sha256"]
                or row["query_ids_sha256"] != first["query_ids_sha256"]
                or row["queries_per_cohort"] != 300 or row["observations"] != 1200):
            raise ValueError("Comparison requires the same queries, cohorts, engine and protocol")
    for seed in expected_seeds:
        if len({row["insertion_ids_sha256"] for row in manifests if row["seed"] == seed}) != 1:
            raise ValueError("Each model must use the same permutation for a given seed")
    if len({items[0]["space"] for items in by_model.values()}) != len(by_model):
        raise ValueError("Each encoder requires a separate vector space")

    report = {}
    for model, items in by_model.items():
        estimates = {}
        for cut in map(str, PROTOCOL["cohorts_percent"]):
            names = {name for item in items for name in item["groups"][cut]}
            algorithms = Counter()
            for name in sorted(names):
                rows = [item["groups"][cut][name] for item in items if name in item["groups"][cut]]
                total = sum(row["queries"] for row in rows)
                algorithms[name] = total
                per_seed = len(rows)
                estimates[name] = {
                    "observations": total, "seeds": per_seed,
                    "recall_at_10": variation([row["ann_recall_at_10"] for row in rows]),
                    "overlap_at_100": variation([row["ann_overlap_at_100"] for row in rows]),
                    "p95_ms": variation([row["engine_client_ms"]["p95"] for row in rows]),
                    "RET-01_passed": None if not name.endswith("/hnsw") or per_seed < len(expected_seeds)
                    or min(row["queries"] for row in rows) < 100 else
                    all(row["ann_recall_at_10"] >= PROTOCOL["ann_recall_at_10_min"]
                        and row["engine_client_ms"]["p95"] < PROTOCOL["vespa_p95_ms_limit"] for row in rows),
                }
            if sum(algorithms.values()) != 1200:
                raise ValueError("Missing cohort observations")
        report[model] = {
            "unique_test_queries": 300, "observations_per_cohort": 1200, "observations": 4800,
            "exact_parity_passed": all(item["exact_parity_passed"] for item in items),
            "estimates": estimates,
            "quality": {
                ranker: {metric: variation([item["metrics"][ranker][metric] for item in items])
                         for metric in items[0]["metrics"][ranker]}
                for ranker in items[0]["metrics"]
            },
        }
    comparison = {}
    matched_engine = matched_encoding = False
    if len(by_model) == 2:
        control, candidate = (sorted(by_model[model], key=lambda row: row["seed"]) for model in PROTOCOL["models"])
        matched_engine = control[0]["engine_host"] == candidate[0]["engine_host"]
        matched_encoding = control[0]["encoding_profile"] == candidate[0]["encoding_profile"]
        for cut in map(str, PROTOCOL["cohorts_percent"]):
            common = set.intersection(*(set(row["groups"][cut]) for row in [*control, *candidate]))
            for name in sorted(common):
                pairs = [(a["groups"][cut][name], b["groups"][cut][name])
                         for a, b in zip(control, candidate, strict=True)]
                if not all(a["queries"] == b["queries"] == 300 for a, b in pairs):
                    continue
                comparison[name] = {
                    "gemma_minus_e5_p95_ms": variation([b["engine_client_ms"]["p95"] - a["engine_client_ms"]["p95"]
                                                         for a, b in pairs]) if matched_engine else None,
                    "gemma_minus_e5_ann_recall_at_10": variation([b["ann_recall_at_10"] - a["ann_recall_at_10"]
                                                                   for a, b in pairs]),
                }
    return {"protocol_sha256": digest(PROTOCOL_PATH), "paired_models": len(by_model) == 2,
            "matched_engine_host": matched_engine, "matched_encoding_profile": matched_encoding,
            "models": report, "paired_deltas_by_algorithm": comparison}


def variation(samples):
    return {"mean": mean(samples), "sample_stddev": stdev(samples) if len(samples) > 1 else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    seed = modes.add_parser("run")
    for key in ("dataset", "embeddings", "test_vectors", "evaluator", "output"):
        seed.add_argument(key, type=Path)
    seed.add_argument("--vespa", required=True)
    seed.add_argument("--seed", type=int, choices=PROTOCOL["insertion_order_seeds"], required=True)
    report = modes.add_parser("aggregate")
    report.add_argument("output", type=Path)
    report.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()
    if args.mode == "run":
        run(args)
    else:
        data = aggregate([json.loads(path.read_bytes()) for path in args.manifests])
        write_json(args.output, data)
        print(json.dumps(data), flush=True)


if __name__ == "__main__":
    main()
