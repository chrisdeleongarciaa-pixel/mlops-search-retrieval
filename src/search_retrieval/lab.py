"""Operator entry point for the synthetic, single-machine retrieval profile."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path

from search_retrieval.adapters.file_objects import FileObjectStore
from search_retrieval.adapters.postgres_events import PostgresEventLedger
from search_retrieval.adapters.postgres_publication import PostgresPublication
from search_retrieval.adapters.vespa import VespaWitness
from search_retrieval.ingestion.publication import PublicationWorker
from search_retrieval.ingestion.submit_event import EventSubmission
from search_retrieval.ingestion.witness import replay_witness
from search_retrieval.query.eligible import EligibleSearch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("feed", type=Path)
    replay.add_argument("corpus", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--max-events", type=int, default=100)
    search = commands.add_parser("search")
    search.add_argument("tenant")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    dsn = os.environ["INGESTION_DSN"]
    if args.command == "replay":
        objects = FileObjectStore(Path(os.environ["INGESTION_OBJECTS"]))
        ledger = PostgresEventLedger(dsn)
        service = EventSubmission(ledger, objects, max_original_bytes=1_048_576)
        result = replay_witness(service, ledger, args.feed, args.corpus)
        print(json.dumps(asdict(result)))
        return int(result.stopped is not None or result.missing_cursor is not None)
    publication = PostgresPublication(dsn)
    index = VespaWitness(os.environ["WITNESS_VESPA_URL"])
    if args.command == "publish":
        if args.max_events <= 0:
            parser.error("--max-events must be positive")
        objects = FileObjectStore(Path(os.environ["INGESTION_OBJECTS"]))
        worker = PublicationWorker(publication, objects, index)
        for _ in range(args.max_events):
            outcome = worker.run_one()
            if outcome is None:
                break
            print(outcome)
        return 0
    result = EligibleSearch(publication, index).search(args.tenant, args.query, k=args.top_k)
    print(json.dumps([asdict(hit) for hit in result.hits], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
