"""In-process HTTP fakes of Vespa and TEI for tests. They implement only what the clients use.

The fake Vespa parses the YQL the client writes: a collection, optional cohort and ACL filters,
and either `userInput` (scored by the number of distinct query terms a document contains) or
`nearestNeighbor` (exact inner product, whatever `approximate` says). It is a protocol fake,
not a ranking reference.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import unquote, urlsplit

import numpy as np


class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.fake = self
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _reply(handler, status: int, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class _VespaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else {}

    def do_POST(self):
        fake, path = self.server.fake, urlsplit(self.path).path
        body = self._body()
        if path.startswith("/document/v1/"):
            with fake.lock:
                if fake.overload > 0:
                    fake.overload -= 1
                    return _reply(self, 429, {"message": "overloaded"})
                fields = body["fields"]
                if "embedding" in fields:
                    fields = {**fields, "embedding": fields["embedding"]["values"]}
                fake.documents[unquote(path.rsplit("/", 1)[1])] = fields
            return _reply(self, 200, {})
        if path == "/search/":
            return _reply(self, 200, fake.search(body))
        return _reply(self, 404, {})

    def do_PUT(self):
        fake, path = self.server.fake, urlsplit(self.path).path
        body = self._body()
        key = unquote(path.rsplit("/", 1)[1])
        with fake.lock:
            if key not in fake.documents:
                return _reply(self, 404, {"message": "no such document"})
            fake.documents[key]["acl_tokens"] = list(body["fields"]["acl_tokens"]["assign"])
        return _reply(self, 200, {})

    def do_GET(self):
        fake, path = self.server.fake, urlsplit(self.path).path
        key = unquote(path.rsplit("/", 1)[1])
        with fake.lock:
            fields = fake.documents.get(key)
        return _reply(self, 200, {"fields": fields}) if fields is not None else _reply(self, 404, {})


class FakeVespa(_Server):
    def __init__(self, *, overload: int = 0):
        self.documents: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.overload = overload
        self.queries: list[dict] = []
        super().__init__(_VespaHandler)

    def _matching(self, yql: str) -> list[dict]:
        collection = re.search(r'collection contains "([^"]+)"', yql).group(1)
        below = re.search(r"cohort_bucket < (\d+)", yql)
        tokens = re.findall(r'acl_tokens contains "([^"]+)"', yql)
        with self.lock:
            rows = [dict(fields) for fields in self.documents.values() if fields["collection"] == collection]
        if below:
            rows = [row for row in rows if row["cohort_bucket"] < int(below.group(1))]
        if tokens:
            rows = [row for row in rows if set(tokens) & set(row["acl_tokens"])]
        return rows

    def search(self, body: dict) -> dict:
        self.queries.append(body)
        yql = body["yql"]
        rows = self._matching(yql)
        coverage = {"full": True, "degraded": None}
        if "limit 0" in yql:
            return {"root": {"fields": {"totalCount": len(rows)}, "coverage": coverage}}
        hits = int(body.get("hits", 10))
        if "nearestNeighbor" in yql:
            query = np.asarray(body["input.query(q)"], dtype=np.float64)
            scored = [(float(np.dot(np.asarray(row["embedding"], dtype=np.float64), query)), row["doc_id"]) for row in rows]
        else:
            terms = set(body["q"].lower().split())
            scored = [(float(len(terms & set(row["text"].lower().split()))), row["doc_id"]) for row in rows]
            scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1]))
        children = [{"relevance": score, "fields": {"doc_id": doc}} for score, doc in scored[:hits]]
        payload = {"root": {"fields": {"totalCount": len(scored)}, "coverage": coverage, "children": children}}
        if body.get("trace.level"):
            payload["trace"] = {"children": [{"message": [{"algorithm": "index top k"}]}]}
        return payload


class _TeiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        fake = self.server.fake
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length))
        if urlsplit(self.path).path != "/embed":
            return _reply(self, 404, {})
        fake.requests.append(body)
        if fake.fail:
            return _reply(self, 503, {"error": "unavailable"})
        data = json.dumps([fake.vector(text) for text in body["inputs"]]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeTei(_Server):
    """Returns `vector(text)` for each input; by default a unit vector derived from the text."""

    def __init__(self, dimensions: int = 8, vector=None):
        self.requests: list[dict] = []
        self.fail = False
        self.dimensions = dimensions
        self.vector = vector or self._default
        super().__init__(_TeiHandler)

    def _default(self, text: str) -> list[float]:
        seed = sum(text.encode("utf-8")) % (2 ** 32)
        row = np.random.default_rng(seed).standard_normal(self.dimensions)
        return (row / np.linalg.norm(row)).astype(np.float32).tolist()
