"""ACL-01: how long a revoked grant stays visible, at delivery and in the engine.

    python evaluation/acl_revocation.py CONFIG --collection LABEL --vectors-dir DIR \
        --projections-dir DIR --vespa-url URL --postgres-dsn DSN [--documents 1000] [--principals 10]

Run on a collection that is not being served (the SciFact projection): it makes documents
private for the duration of the measurement and makes them public again at the end.

For each document: grant it to one principal, check that the principal retrieves it first with
its own vector through the retrieval service, commit the revocation in the authority, then
apply the engine's partial update. From the moment the commit returns, the service is queried
until the document is gone (delivery exclusion) and the engine's filtered search is polled
until it is gone (engine exclusion). Any service response after the commit that still holds
the document is a leak. ACL-01 passes when no response leaks and every delivery exclusion
takes less than 1 s, over at least 1,000 revocations.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from time import monotonic, sleep

import numpy as np

from search_retrieval.adapters.postgres_acl import PUBLIC, PostgresAcl
from search_retrieval.adapters.vespa_passages import VespaPassages
from search_retrieval.experiment.manifests import inside, parse_manifest
from search_retrieval.query.eligible import SearchUnavailable
from search_retrieval.serving.service import RetrievalService
from ann import open_sets
from ann_sweep import parse_sweep
from embed import ROOT
from latency import summary
from vector_store import _write_atomic


LIMIT_S = 1.0
ENGINE_BUDGET_S = 30.0


class FixedEncoder:
    """Returns a preset vector, so the service searches with a document's own embedding."""

    def __init__(self):
        self.vector = None

    def encode_queries(self, queries):
        return [self.vector]


def measure_one(service: RetrievalService, engine: VespaPassages, authority, encoder: FixedEncoder,
                collection: str, doc_id: str, principal: str, vector: list[float]) -> dict:
    encoder.vector = vector
    before = [doc for doc, _ in service.search("revocation probe", route="dense", k=5, principal=principal).hits]
    if not before or before[0] != doc_id:
        return {"doc_id": doc_id, "skipped": "the principal does not retrieve the document first"}
    authority.revoke(doc_id, principal)
    committed = monotonic()
    with ThreadPoolExecutor(1) as pool:
        update = pool.submit(engine.update_tokens, collection, doc_id, [])
        leaks, delivery = 0, None
        while delivery is None and monotonic() - committed < LIMIT_S * 5:
            hits = [doc for doc, _ in service.search("revocation probe", route="dense", k=5, principal=principal).hits]
            if doc_id in hits:
                leaks += 1
            else:
                delivery = monotonic() - committed
        update.result()
    engine_gone = None
    while engine_gone is None and monotonic() - committed < ENGINE_BUDGET_S:
        hits, _ = engine.dense(collection, vector, 5, tokens=[PUBLIC, principal])
        if doc_id not in {hit.doc_id for hit in hits}:
            engine_gone = monotonic() - committed
        else:
            sleep(0.005)
    return {"doc_id": doc_id, "leaks": leaks, "delivery_s": delivery, "engine_s": engine_gone}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--vectors-dir", type=Path, required=True)
    parser.add_argument("--projections-dir", type=Path, required=True)
    parser.add_argument("--vespa-url", required=True)
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--documents", type=int, default=1000)
    parser.add_argument("--principals", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = parse_sweep(inside(ROOT, args.config).read_bytes())
        documents, _ = open_sets(config, args.vectors_dir)
        projection, _ = parse_manifest((args.projections_dir / args.collection / "manifest.json").read_bytes(),
                                       "vespa-projection")
        if projection["vector_set"] != documents.digest:
            raise ValueError("The collection projects another vector set")
        if args.documents > documents.n or args.documents < 1:
            raise ValueError("More documents requested than the collection holds")
        engine = VespaPassages(args.vespa_url, timeout=10)
        authority = PostgresAcl(args.postgres_dsn, tenant="bench", source=args.collection)
        encoder = FixedEncoder()
        service = RetrievalService(engine, encoder, authority, args.collection, branch_depth=10)
        order = sorted(range(documents.n), key=documents.ids.__getitem__)[:args.documents]
        grants = {documents.ids[position]: f"principal-{index % args.principals}" for index, position in enumerate(order)}
        authority.make_private(grants)
        for doc_id, principal in grants.items():
            engine.update_tokens(args.collection, doc_id, [principal])
        sleep(1.0)
        results = []
        try:
            for position in order:
                doc_id = documents.ids[position]
                vector = documents.take(np.array([position]), np.float32)[0].tolist()
                results.append(measure_one(service, engine, authority, encoder, args.collection, doc_id,
                                           grants[doc_id], vector))
        finally:
            for doc_id in grants:
                engine.update_tokens(args.collection, doc_id, [PUBLIC])
            authority.make_public(list(grants))
        measured = [item for item in results if "skipped" not in item]
        delivery = np.array([item["delivery_s"] for item in measured if item["delivery_s"] is not None])
        engine_s = np.array([item["engine_s"] for item in measured if item["engine_s"] is not None])
        leaks = sum(item["leaks"] for item in measured)
        report = {
            "collection": args.collection, "revocations": len(measured), "skipped": len(results) - len(measured),
            "leaks": leaks, "limit_s": LIMIT_S,
            "delivery_exclusion_s": summary(delivery) if delivery.size else None,
            "delivery_never_excluded": len(measured) - int(delivery.size),
            "engine_exclusion_s": summary(engine_s) if engine_s.size else None,
            "engine_never_excluded": len(measured) - int(engine_s.size),
            "passed": bool(len(measured) >= 1000 and leaks == 0 and delivery.size == len(measured)
                           and (delivery < LIMIT_S).all()),
            "scope": "search and delivery through the retrieval service; no generation context is served",
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(args.out, (json.dumps({"report": report, "revocations": results}, indent=2) + "\n").encode())
        print(json.dumps(report))
        return 0 if report["passed"] else 2
    except (SearchUnavailable, ValueError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
