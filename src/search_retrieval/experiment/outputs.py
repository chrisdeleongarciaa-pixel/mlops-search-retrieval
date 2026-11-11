"""Deterministic run artifacts: TREC run, per-query records and the run manifest."""

from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

from search_retrieval.contracts.canonical import canonical_json
from search_retrieval.experiment.run_config import ResolvedRun


RUN, PER_QUERY, MANIFEST, TIMINGS = "run.trec", "per-query.jsonl", "manifest.json", "timings.json"


def trec_run(rankings: dict[str, list[tuple[str, float]]], tag: str) -> bytes:
    """`qid Q0 doc rank score tag`, queries in ID order, ranks as given by the ranker."""
    lines = []
    for qid in sorted(rankings):
        seen = set()
        for rank, (doc, score) in enumerate(rankings[qid], 1):
            if any(not value or len(value.split()) != 1 for value in (qid, doc)):
                raise ValueError("Query and document IDs must be single tokens")
            if doc in seen or type(score) is not float or not isfinite(score):
                raise ValueError(f"Duplicate document or invalid score for query {qid}")
            seen.add(doc)
            lines.append(f"{qid} Q0 {doc} {rank} {score!r} {tag}\n")
    return "".join(lines).encode("utf-8")


def parse_trec_run(data: bytes) -> dict[str, list[tuple[str, float]]]:
    rankings: dict[str, list[tuple[str, float]]] = {}
    for line in data.decode("utf-8").splitlines():
        qid, literal, doc, rank, score, _ = line.split(" ")
        hits = rankings.setdefault(qid, [])
        if literal != "Q0" or int(rank) != len(hits) + 1:
            raise ValueError("Malformed TREC run")
        hits.append((doc, float(score)))
    return rankings


def write_run(output: Path, resolved: ResolvedRun, command: str, rankings, records: list[dict],
              metrics: dict, environment: dict, timings: dict) -> dict:
    """Write the artifacts into a new directory. Timings stay outside the reproducible set."""
    run = trec_run(rankings, resolved.config["run"]["id"])
    per_query = b"".join(canonical_json(record) + b"\n" for record in records)
    manifest = {
        "schema_version": 1, "command": command, "run_id": resolved.config["run"]["id"],
        "configuration": {"path": resolved.path, "values": resolved.config},
        "keys": resolved.keys(),
        "legacy_space_ids": {name: table["legacy_id"]
                             for name, table in sorted(resolved.config.get("space", {}).items())
                             if "legacy_id" in table},
        "split": resolved.split, "environment": environment,
        "queries": len(records), "metrics": metrics,
        "outputs": {RUN: sha256(run).hexdigest(), PER_QUERY: sha256(per_query).hexdigest()},
        "observations": TIMINGS,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / RUN).write_bytes(run)
    (output / PER_QUERY).write_bytes(per_query)
    (output / MANIFEST).write_bytes(
        (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    )
    (output / TIMINGS).write_bytes(
        (json.dumps(timings, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    )
    return manifest


def verify_outputs(output: Path) -> dict:
    """Recompute the hashes that the manifest records for its run and per-query files."""
    manifest = json.loads((output / MANIFEST).read_bytes())
    for name, expected in manifest["outputs"].items():
        if sha256((output / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"{name} does not match the run manifest")
    return manifest
