"""Exact oracle and ANN sweeps over the vector sets of one registered space.

    python evaluation/ann.py oracle CONFIG --vectors-dir DIR --out-dir DIR [--device cuda]
    python evaluation/ann.py sweep CONFIG --vectors-dir DIR --out-dir DIR [--only LABEL ...]
    python evaluation/ann.py verify-oracle DIRECTORY

CONFIG is a sweep TOML (see config/ann/). The vector sets are the ones written by
`embed.py encode` for the space, corpus, transform and queries named in CONFIG. The oracle
of every cohort is written once and reused; a finished build is kept and skipped on a rerun.
"""

import argparse
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

import numpy as np

from search_retrieval.contracts.cohorts import COHORT_RULE, cohort_buckets, cohort_mask
from search_retrieval.experiment.manifests import ManifestError, inside, read_manifest, require
from search_retrieval.experiment.provenance import ProvenanceError, _git, verify_clean, verify_repository
from search_retrieval.experiment.run_config import CORPUS, QUERY_SET, ConfigError, read_space
from ann_sweep import SweepError, completed, expand, frontiers, json_bytes, parse_sweep, run_build
from canonical import CanonicalRows
from embed import ROOT, set_directory
from oracle import FlatSearcher, GPU_MAX_K, OracleError, exact_top_k, read_oracle, write_oracle
from vector_store import Identity, VectorStoreError, _write_atomic


ORACLE_CODE = ("evaluation/oracle.py", "evaluation/canonical.py", "evaluation/vector_store.py",
               "src/search_retrieval/contracts/cohorts.py")
SWEEP_CODE = ORACLE_CODE + ("evaluation/ann_sweep.py",)


def code_digest(paths) -> str:
    """Digest of the measurement code. Results computed by other code are not reused."""
    digest = sha256()
    for relative in paths:
        data = inside(ROOT, relative).read_bytes()
        digest.update(f"{relative}\t{len(data)}\n".encode("utf-8"))
        digest.update(data)
    return digest.hexdigest()


def environment(device: str) -> dict:
    packages = {}
    for name in ("faiss-cpu", "faiss-gpu-cuvs", "numpy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            pass
    return {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(),
            "cpu_count": os.cpu_count(), "device": device, "packages": packages}


def open_sets(config: dict, vectors_dir: Path):
    """Resolve and verify the document and query vector sets that CONFIG names."""
    inputs = config["inputs"]
    _, space = read_space(ROOT, inputs["space"])
    corpus, corpus_digest = read_manifest(ROOT, inputs["corpus"], "corpus")
    require(corpus, "corpus", *CORPUS)
    _, transform_digest = read_manifest(ROOT, inputs["transform"], "transform")
    query_set, _ = read_manifest(ROOT, inputs["queries"], "query-set")
    require(query_set, "query-set", *QUERY_SET)
    documents = CanonicalRows(set_directory(vectors_dir, space.digest, Identity(
        space, "document", {"corpus": corpus_digest, "transform": transform_digest})))
    queries = CanonicalRows(set_directory(vectors_dir, space.digest, Identity(
        space, "query", {"queries": query_set["queries_sha256"]})))
    if documents.n != corpus["documents"]:
        raise VectorStoreError("The document vector set does not cover the corpus")
    return documents, queries


def oracle_directory(out_dir: Path, documents: CanonicalRows, queries: CanonicalRows, permille: int) -> Path:
    return out_dir / f"{documents.digest[:16]}-{queries.digest[:16]}" / "oracle" / f"cohort-{permille:04d}"


def command_oracle(args, config: dict, commit: str) -> dict:
    oracle = config["oracle"]
    if args.device.startswith("cuda") and oracle["k"] > GPU_MAX_K:
        raise ConfigError(f"The GPU searcher returns at most {GPU_MAX_K} candidates")
    documents, queries = open_sets(config, args.vectors_dir)
    buckets = cohort_buckets(documents.ids)
    implementation = code_digest(ORACLE_CODE)
    written = {}
    for permille in sorted(oracle["cohorts_permille"], reverse=True):
        directory = oracle_directory(args.out_dir, documents, queries, permille)
        if (directory / "manifest.json").exists():
            body, _, _ = read_oracle(directory)
            if (body["documents"], body["queries"], body["k"], body.get("implementation_sha256")) == (
                    documents.digest, queries.digest, oracle["k"], implementation):
                written[permille] = {"directory": str(directory), "reused": True}
                continue
            raise OracleError(f"{directory} holds an oracle of other inputs or code; move it aside to recompute")
        eligible = np.nonzero(cohort_mask(buckets, permille))[0].astype(np.int64)
        started = perf_counter()
        searcher = FlatSearcher(documents, eligible, args.device) if eligible.size else None
        indexed = perf_counter() - started
        result = exact_top_k(queries, documents, eligible, oracle["k"], searcher,
                             initial=oracle["initial_candidates"], batch=oracle["query_batch"])
        observation = {"code_commit": commit, "seconds": {"index": indexed, "total": perf_counter() - started},
                       "initial_candidates": oracle["initial_candidates"], **environment(args.device)}
        sealed = write_oracle(directory, result, documents=documents, queries=queries, permille=permille,
                              rule=COHORT_RULE, eligible=eligible, k=oracle["k"], implementation=implementation,
                              observation=observation)
        del searcher
        written[permille] = {"directory": str(directory), "oracle": sealed["sha256"], "eligible": int(eligible.size),
                             "states": result.state_counts(), "observed_max_error": result.observed_max_error,
                             "epsilon": result.epsilon}
        print(json.dumps({"cohort_permille": permille, **written[permille]}), flush=True)
    return {"documents": documents.digest, "queries": queries.digest, "oracles": written}


def command_sweep(args, config: dict, commit: str, config_bytes: bytes) -> dict:
    documents, queries = open_sets(config, args.vectors_dir)
    buckets = cohort_buckets(documents.ids)
    builds = expand(config)
    if args.only:
        unknown = set(args.only) - {build.label for build in builds}
        if unknown:
            raise ConfigError(f"Unknown builds: {sorted(unknown)}")
        builds = [build for build in builds if build.label in args.only]
    cohorts = sorted({permille for build in builds for permille in build.cohorts}, reverse=True)
    oracles, masks, oracle_digests = {}, {}, {}
    for permille in cohorts:
        directory = oracle_directory(args.out_dir, documents, queries, permille)
        body, rows, scores = read_oracle(directory)
        if (body["documents"], body["queries"], body.get("implementation_sha256")) != (
                documents.digest, queries.digest, code_digest(ORACLE_CODE)):
            raise OracleError(f"{directory} holds an oracle of other inputs or code; run the oracle command")
        oracles[permille] = (rows, scores)
        masks[permille] = cohort_mask(buckets, permille)
        oracle_digests[permille] = sha256((directory / "manifest.json").read_bytes()).hexdigest()
    measurement = code_digest(SWEEP_CODE)
    sweep_dir = (args.out_dir / f"{documents.digest[:16]}-{queries.digest[:16]}"
                 / f"sweep-{sha256(config_bytes).hexdigest()[:16]}-{measurement[:16]}")
    records = []
    for build in builds:
        directory = sweep_dir / build.label
        record = completed(directory)
        if record is None:
            print(f"build {build.label}", flush=True)
            record = run_build(build, documents, queries, oracles, masks, config["measure"], directory)
        records.append(record)
    points = [json.loads(line) for record in records
              for line in (sweep_dir / record["build"] / "points.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = {
        "code_commit": commit, "measurement_code_sha256": measurement,
        "config": {"path": args.config, "sha256": sha256(config_bytes).hexdigest()},
        "documents": documents.digest, "queries": queries.digest, "oracles": oracle_digests,
        "builds": {record["build"]: {key: record[key] for key in ("structure_sha256", "estimated_bytes_per_vector",
                                                                   "build_seconds", "points_sha256")}
                   for record in records},
        "frontiers": frontiers(points, config["measure"]["recall_at"]), "environment": environment("cpu"),
    }
    _write_atomic(sweep_dir / "summary.json", json_bytes(summary))
    return {"sweep": str(sweep_dir), "builds": len(records), "points": len(points)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("oracle", "sweep"):
        command = commands.add_parser(name)
        command.add_argument("config")
        command.add_argument("--vectors-dir", type=Path, required=True)
        command.add_argument("--out-dir", type=Path, required=True)
        if name == "oracle":
            command.add_argument("--device", default="cpu")
        else:
            command.add_argument("--only", nargs="+")
    commands.add_parser("verify-oracle").add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify-oracle":
            body, _, _ = read_oracle(args.directory)
            result = {"verified": str(args.directory), "cohort_permille": body["cohort"]["permille"], "k": body["k"]}
        else:
            verify_repository(ROOT)
            verify_clean(ROOT)
            commit = _git(ROOT, "rev-parse", "HEAD")
            config_bytes = inside(ROOT, args.config).read_bytes()
            config = parse_sweep(config_bytes)
            result = (command_oracle(args, config, commit) if args.command == "oracle"
                      else command_sweep(args, config, commit, config_bytes))
    except (ConfigError, ManifestError, ProvenanceError, VectorStoreError, OracleError, SweepError,
            ValueError, OSError, RuntimeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
