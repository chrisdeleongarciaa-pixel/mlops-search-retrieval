"""Deterministic run artifacts: TREC run, per-query records and the run manifest."""

from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import shutil

from search_retrieval.contracts.canonical import canonical_json
from search_retrieval.experiment.run_config import ResolvedRun


RUN, PER_QUERY, MANIFEST, TIMINGS = "run.trec", "per-query.jsonl", "manifest.json", "timings.json"
Hits = list[tuple[str, float]]


def trec_lines(qid: str, hits: Hits, tag: str) -> bytes:
    """`qid Q0 doc rank score tag`. The rank column is the order given by the ranker."""
    lines = []
    seen = set()
    for rank, (doc, score) in enumerate(hits, 1):
        if any(not value or len(value.split()) != 1 for value in (qid, doc)):
            raise ValueError("Query and document IDs must be single tokens")
        if doc in seen or type(score) is not float or not isfinite(score):
            raise ValueError(f"Duplicate document or invalid score for query {qid}")
        seen.add(doc)
        lines.append(f"{qid} Q0 {doc} {rank} {score!r} {tag}\n")
    return "".join(lines).encode("utf-8")


def trec_run(rankings: dict[str, Hits], tag: str) -> bytes:
    """A complete run with queries in ID order."""
    return b"".join(trec_lines(qid, rankings[qid], tag) for qid in sorted(rankings))


def read_trec_run(lines: Iterable[str]) -> Iterator[tuple[str, Hits]]:
    """Yield each query of a TREC run in file order. Ranks must count from one within a query."""
    current, hits, finished = None, [], set()
    for line in lines:
        fields = line.split()
        if len(fields) != 6 or fields[1] != "Q0":
            raise ValueError("Malformed TREC run")
        qid, _, doc, rank, score, _ = fields
        if qid != current:
            if current is not None:
                yield current, hits
                finished.add(current)
            if qid in finished:
                raise ValueError(f"Query {qid} is not contiguous in the TREC run")
            current, hits = qid, []
        if int(rank) != len(hits) + 1:
            raise ValueError(f"Ranks of query {qid} do not count from one")
        hits.append((doc, float(score)))
    if current is not None:
        yield current, hits


def parse_trec_run(data: bytes) -> dict[str, Hits]:
    return dict(read_trec_run(data.decode("utf-8").splitlines()))


def write_run(output: Path, resolved: ResolvedRun, command: str,
              stream: Iterable[tuple[str, Hits, dict]], environment: dict,
              summarize: Callable[[], tuple[dict, dict]]) -> dict:
    """Stream `(qid, hits, record)` in increasing query ID order into a new directory.

    `summarize` runs after the stream ends and returns aggregate metrics and timings.
    Timings stay outside the reproducible set. A failed run leaves no output directory.
    """
    partial = output.with_name(output.name + ".partial")
    if output.exists():
        raise FileExistsError(f"Run output already exists: {output}")
    partial.mkdir(parents=True, exist_ok=False)
    try:
        tag = resolved.config["run"]["id"]
        digests = {RUN: sha256(), PER_QUERY: sha256()}
        previous, queries = None, 0
        with (partial / RUN).open("wb") as run, (partial / PER_QUERY).open("wb") as per_query:
            for qid, hits, record in stream:
                if previous is not None and qid <= previous:
                    raise ValueError("Queries must arrive in strictly increasing ID order")
                previous, queries = qid, queries + 1
                for target, name, data in ((run, RUN, trec_lines(qid, hits, tag)),
                                           (per_query, PER_QUERY, canonical_json(record) + b"\n")):
                    target.write(data)
                    digests[name].update(data)
        metrics, timings = summarize()
        manifest = {
            "schema_version": 1, "command": command, "run_id": tag,
            "configuration": {"path": resolved.path, "values": resolved.config},
            "keys": resolved.keys(),
            "legacy_space_ids": {name: table["legacy_id"]
                                 for name, table in sorted(resolved.config.get("space", {}).items())
                                 if "legacy_id" in table},
            "split": resolved.split, "environment": environment,
            "queries": queries, "metrics": metrics,
            "outputs": {name: digest.hexdigest() for name, digest in digests.items()},
            "observations": TIMINGS,
        }
        (partial / MANIFEST).write_bytes(
            (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        )
        (partial / TIMINGS).write_bytes(
            (json.dumps(timings, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        )
        os.replace(partial, output)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return manifest


def verify_outputs(output: Path) -> dict:
    """Recompute the hashes that the manifest records for its run and per-query files."""
    manifest = json.loads((output / MANIFEST).read_bytes())
    for name, expected in manifest["outputs"].items():
        digest = sha256()
        with (output / name).open("rb") as source:
            while block := source.read(1 << 20):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"{name} does not match the run manifest")
    return manifest
