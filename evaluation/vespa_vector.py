"""Measure paired request stages on the mutable PostgreSQL/Vespa witness."""

import argparse
import json
import os
from pathlib import Path
import platform
from time import perf_counter_ns

from search_retrieval.adapters.file_objects import FileObjectStore
from search_retrieval.adapters.postgres_publication import PostgresPublication
from search_retrieval.adapters.vespa import VespaWitness
from search_retrieval.ingestion.publication import PublicationWorker
from search_retrieval.query.eligible import SearchUnavailable
from search_retrieval.query.vector import VectorSearch
from scifact_bm25 import digest
from scifact_embeddings import SPACE, encoder, write_json
from vespa_lexical import percentiles


def measure(search, workload, mode, *, warmup=3, repeats=20, depth=100):
    tenant = workload["tenant"]
    snapshot = search.authority.eligibility(tenant)
    for _ in range(warmup):
        for query in workload["queries"]:
            search.search(tenant, query["text"], mode=mode, k=depth)
    rows = []
    for repeat in range(1, repeats + 1):
        for query in workload["queries"]:
            request_id = f"{mode}/{repeat}/{query['id']}"
            attempts = []
            start = perf_counter_ns()
            try:
                result = search.search(tenant, query["text"], mode=mode, k=depth, attempts=attempts)
                status = "complete" if result.eligibility == snapshot else "changed"
                candidates = [{"id": hit.fragment_id, "score": hit.score} for hit in result.hits]
            except SearchUnavailable as error:
                status, candidates = str(error), []
            elapsed = (perf_counter_ns() - start) / 1_000_000
            rows.append({
                "request_id": request_id, "query_id": query["id"], "attempts": attempts,
                "retrieval_ms": elapsed, "status": status, "candidates": candidates,
                "cache": {"query": "disabled", "results": "disabled"},
            })
    complete = [row for row in rows if row["status"] == "complete"]
    stages = sorted({key for row in complete for attempt in row["attempts"]
                     for key in attempt if key.endswith("_ms")})
    retrieval = percentiles([row["retrieval_ms"] for row in complete])
    manifest = {
        "mode": mode, "generation": snapshot.generation, "eligible_projections": sorted(snapshot.projections),
        "clients": 1, "requests": len(rows), "complete": len(complete), "depth": depth,
        "warmup": warmup, "repeats": repeats, "query_cache": False, "result_cache": False,
        "retrieval_client_ms": retrieval, "stage_ms": {
            stage: percentiles([attempt[stage] for row in complete for attempt in row["attempts"] if stage in attempt])
            for stage in stages
        },
        "SRV-01": {
            "scope": "single-client retrieval component",
            "passed": bool(complete) and len(complete) == len(rows) and retrieval["p95"] < 50,
        },
    }
    return manifest, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("model", "workload", "output"):
        parser.add_argument(field, type=Path)
    args = parser.parse_args()
    workload = json.loads(args.workload.read_text(encoding="utf-8"))
    authority = PostgresPublication(os.environ["INGESTION_DSN"], include_fragments=True)
    adapter = encoder(args.model, "cpu", 2)
    index = VespaWitness(os.environ["WITNESS_VESPA_URL"], encoder=adapter)
    originals = FileObjectStore(Path(os.environ["INGESTION_OBJECTS"]))
    worker = PublicationWorker(authority, originals, index, vector_space=SPACE.id)
    while (outcome := worker.run_one()) is not None:
        if outcome != "published":
            raise RuntimeError(f"Unexpected vector publication outcome: {outcome}")
    generation = authority.eligibility(workload["tenant"]).generation
    search = VectorSearch(authority, index, adapter, generation)
    args.output.mkdir()
    for mode in ("dense", "hybrid"):
        manifest, rows = measure(search, workload, mode)
        path = args.output / f"{mode}-samples.json"
        write_json(path, rows)
        manifest.update({
            "samples_sha256": digest(path), "workload_sha256": digest(args.workload),
            "model_space_id": SPACE.id, "model_state": "warm", "inference_precision": "FP32",
            "device": "cpu", "torch": adapter.torch.__version__, "platform": platform.platform(),
        })
        write_json(args.output / f"{mode}-manifest.json", manifest)
        print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
