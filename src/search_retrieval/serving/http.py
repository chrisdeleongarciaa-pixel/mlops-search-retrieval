"""HTTP front end of the retrieval service.

    python -m search_retrieval.serving.http --vespa-url URL --collection LABEL --space MANIFEST \
        [--tei-url URL] [--postgres-dsn DSN --tenant T --source S] [--fusion rrf|convex:ALPHA] \
        [--host 127.0.0.1 --port 8090]

POST /search  {"query": str, "route": "bm25"|"dense"|"hybrid", "k": int, "principal": str?}
              200 {"hits": [[doc_id, score], ...], "stages_ms": {...}, "removed_by_authority": n}
              400 invalid request; 503 a dependency failed (the request is refused, never partial)
GET  /health  200 {"collection": LABEL}
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from search_retrieval.adapters.tei import TeiEncoder, TeiSpace
from search_retrieval.adapters.vespa_passages import VespaPassages
from search_retrieval.query.eligible import SearchUnavailable
from search_retrieval.serving.service import Fusion, PublicAuthority, RetrievalService


MAX_BODY = 64 * 1024


def handler_for(service: RetrievalService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _reply(self, status: int, body: dict) -> None:
            data = json.dumps(body, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, {"collection": service.collection})
            return self._reply(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/search":
                return self._reply(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._reply(400, {"error": "missing or oversized body"})
            try:
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict) or set(request) - {"query", "route", "k", "principal"}:
                    raise ValueError("unknown fields")
                principal = request.get("principal")
                if principal is not None and (type(principal) is not str or not principal):
                    raise ValueError("principal must be a nonempty string")
                k = request.get("k", 10)
                if type(k) is not int or type(request.get("query")) is not str:
                    raise ValueError("query must be a string and k an integer")
                answer = service.search(request["query"], route=request.get("route", "hybrid"), k=k, principal=principal)
            except (ValueError, KeyError) as error:
                return self._reply(400, {"error": str(error)})
            except SearchUnavailable as error:
                return self._reply(503, {"error": str(error)})
            return self._reply(200, {"hits": answer.hits, "stages_ms": answer.stages_ms,
                                     "removed_by_authority": answer.removed_by_authority})

    return Handler


def build(args) -> RetrievalService:
    from search_retrieval.experiment.manifests import parse_manifest

    body, _ = parse_manifest(Path(args.space).read_bytes(), "space")
    encoder = TeiEncoder(args.tei_url, TeiSpace.from_body(body), timeout=args.encode_timeout) if args.tei_url else None
    if args.postgres_dsn:
        from search_retrieval.adapters.postgres_acl import PostgresAcl

        authority = PostgresAcl(args.postgres_dsn, tenant=args.tenant, source=args.source)
    else:
        authority = PublicAuthority()
    kind, _, alpha = args.fusion.partition(":")
    return RetrievalService(VespaPassages(args.vespa_url, timeout=args.engine_timeout), encoder, authority,
                            args.collection, fusion=Fusion(kind, float(alpha) if alpha else 0.5),
                            branch_depth=args.branch_depth, explore=args.explore)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vespa-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--tei-url")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--tenant", default="bench")
    parser.add_argument("--source", default="litsearch")
    parser.add_argument("--fusion", default="rrf")
    parser.add_argument("--branch-depth", type=int, default=100)
    parser.add_argument("--explore", type=int, default=0)
    parser.add_argument("--engine-timeout", type=float, default=2.0)
    parser.add_argument("--encode-timeout", type=float, default=2.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(build(args)))
    server.daemon_threads = True
    print(json.dumps({"listening": f"http://{args.host}:{server.server_address[1]}", "collection": args.collection}),
          flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
