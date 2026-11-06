"""Capture the single-client LX-01 witness baseline on a fixed generation."""

import argparse
from hashlib import sha256
import json
from math import ceil
import os
from pathlib import Path
import platform
from time import perf_counter_ns

from search_retrieval.adapters.postgres_publication import PostgresPublication
from search_retrieval.adapters.vespa import VespaWitness
from search_retrieval.query.eligible import EligibleSearch, SearchUnavailable


class TimedVespa(VespaWitness):
    def __init__(self, url: str):
        super().__init__(url)
        self.calls_ms: list[float] = []

    def lexical(self, eligibility, query, k, timeout):
        start = perf_counter_ns()
        try:
            return super().lexical(eligibility, query, k, timeout)
        finally:
            self.calls_ms.append((perf_counter_ns() - start) / 1_000_000)


def percentiles(samples: list[float]) -> dict[str, float | None]:
    ordered = sorted(samples)
    return {
        f"p{p}": ordered[ceil(len(ordered) * p / 100) - 1] if ordered else None
        for p in (50, 95, 99)
    }


def measure(authority, index, workload, *, warmup: int, repeats: int, k: int):
    tenant = workload["tenant"]
    queries = workload["queries"]
    if (
        workload["version"] != 1 or not queries
        or len({query["id"] for query in queries}) != len(queries)
        or warmup < 0 or repeats <= 0 or not 1 <= k <= 100
    ):
        raise ValueError("Invalid LX-01 workload or run size")
    before = authority.eligibility(tenant)
    if not before.projections:
        raise ValueError("Publish the test corpus before measuring")
    search = EligibleSearch(authority, index)
    for _ in range(warmup):
        for query in queries:
            if search.search(tenant, query["text"], k=k).eligibility != before:
                raise ValueError("Eligibility changed during warmup")

    rows = []
    consistent = True
    window = perf_counter_ns()
    for repetition in range(1, repeats + 1):
        for query in queries:
            index.calls_ms.clear()
            hits = ()
            start = perf_counter_ns()
            try:
                result = search.search(tenant, query["text"], k=k)
                hits = result.hits
                status = "complete" if hits else "complete_empty"
                consistent = consistent and result.eligibility == before
            except SearchUnavailable:
                status = "error"
            elapsed = (perf_counter_ns() - start) / 1_000_000
            rows.append({
                "query_id": query["id"], "kind": query["kind"], "repeat": repetition,
                "status": status, "retrieval_ms": elapsed, "engine_calls_ms": list(index.calls_ms),
                "candidates": [
                    {"projection_id": h.projection_id, "fragment_id": h.fragment_id, "score": h.score}
                    for h in hits
                ],
            })
    seconds = (perf_counter_ns() - window) / 1_000_000_000
    try:
        consistent = authority.eligibility(tenant) == before and consistent
    except SearchUnavailable:
        consistent = False
    complete = [row for row in rows if row["status"] != "error"]
    manifest = {
        "experiment": "LX-01", "profile": "single-client-witness", "tenant": tenant,
        "generation": before.generation, "eligible_projections": sorted(before.projections),
        "snapshot_unchanged_at_observed_reads": consistent, "top_k": k, "warmup": warmup,
        "repeats": repeats, "requests": len(rows), "complete": len(complete),
        "empty": sum(row["status"] == "complete_empty" for row in rows),
        "error_rate": (len(rows) - len(complete)) / len(rows),
        "completed_per_second": len(complete) / seconds if seconds else None,
        "retrieval_client_ms": percentiles([row["retrieval_ms"] for row in complete]),
        "engine_client_ms": percentiles([
            duration for row in complete for duration in row["engine_calls_ms"]
        ]),
    }
    return manifest, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory must be new")
    raw = args.workload.read_bytes()
    manifest, rows = measure(
        PostgresPublication(os.environ["INGESTION_DSN"]),
        TimedVespa(os.environ["WITNESS_VESPA_URL"]),
        json.loads(raw), warmup=args.warmup, repeats=args.repeats, k=args.top_k,
    )
    manifest.update({
        "workload_sha256": sha256(raw).hexdigest(),
        "code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "platform": platform.platform(), "python": platform.python_version(),
    })
    args.output.mkdir()
    for name, value in (("manifest", manifest), ("samples", rows)):
        (args.output / f"{name}.json").write_text(
            json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    print(json.dumps(manifest, allow_nan=False))
    return int(not manifest["snapshot_unchanged_at_observed_reads"] or manifest["error_rate"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
