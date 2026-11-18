"""HTTP client for the `passage` document type: feed, count, read back and query a collection.

A collection is one projection of a canonical vector set and its corpus text. Requests use
keep-alive connections, one per thread. Feeding retries overload responses (429, 503, 504)
with exponential backoff; a full content node (507) or any other error stops the feed.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import http.client
import json
from math import isfinite
import threading
from time import monotonic, sleep
from urllib.parse import quote, urlencode, urlsplit

from search_retrieval.query.eligible import SearchUnavailable


NAMESPACE, DOCTYPE = "bench", "passage"
RETRY = {429, 503, 504}


def literal(value: str) -> str:
    """A YQL string literal."""
    return json.dumps(value, ensure_ascii=False)


def document_id(collection: str, doc_id: str) -> str:
    return f"{collection}.{doc_id}"


@dataclass(frozen=True)
class Passage:
    doc_id: str
    text: str
    embedding: list[float] | None
    cohort_bucket: int
    acl_tokens: tuple[str, ...] = ("public",)


@dataclass(frozen=True)
class Hit:
    doc_id: str
    score: float


class VespaPassages:
    def __init__(self, url: str, *, timeout: float = 30.0):
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("Expected an http:// Vespa endpoint")
        self.host, self.port, self.timeout = parts.hostname, parts.port or 80, timeout
        self._local = threading.local()

    def _connection(self) -> http.client.HTTPConnection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return connection

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        for attempt in (1, 2):
            connection = self._connection()
            try:
                connection.request(method, path, data, {"Content-Type": "application/json"} if data else {})
                response = connection.getresponse()
                payload = response.read()
                return response.status, (json.loads(payload) if payload else {})
            except (http.client.HTTPException, OSError) as error:
                # A keep-alive connection closed by the server: reconnect once.
                connection.close()
                self._local.connection = None
                if attempt == 2:
                    raise SearchUnavailable(f"Vespa request failed: {method} {path}") from error
        raise AssertionError("unreachable")

    # Feeding.

    def put(self, collection: str, passage: Passage, *, retries: int = 8) -> None:
        fields = {"collection": collection, "doc_id": passage.doc_id, "text": passage.text,
                  "cohort_bucket": passage.cohort_bucket, "acl_tokens": list(passage.acl_tokens)}
        if passage.embedding is not None:
            fields["embedding"] = {"values": passage.embedding}
        path = f"/document/v1/{NAMESPACE}/{DOCTYPE}/docid/{quote(document_id(collection, passage.doc_id), safe='')}"
        delay = 0.05
        for _ in range(retries):
            status, payload = self.request("POST", path, {"fields": fields})
            if status == 200:
                return
            if status not in RETRY:
                raise SearchUnavailable(f"Vespa refused {passage.doc_id}: {status} {payload.get('message', '')}")
            sleep(delay)
            delay = min(delay * 2, 5.0)
        raise SearchUnavailable(f"Vespa stayed overloaded while feeding {passage.doc_id}")

    def feed(self, collection: str, passages, *, threads: int = 16, progress=None) -> int:
        """Feed every passage, `threads` requests in flight. Returns the number fed."""
        fed = 0
        window = threads * 64
        with ThreadPoolExecutor(threads) as pool:
            pending = []
            for passage in passages:
                pending.append(pool.submit(self.put, collection, passage))
                if len(pending) >= window:
                    for future in pending:
                        future.result()
                    fed += len(pending)
                    pending = []
                    if progress:
                        progress(fed)
            for future in pending:
                future.result()
            fed += len(pending)
        return fed

    def update_tokens(self, collection: str, doc_id: str, tokens: list[str]) -> None:
        """Replace the ACL tokens of one document with a partial update."""
        path = f"/document/v1/{NAMESPACE}/{DOCTYPE}/docid/{quote(document_id(collection, doc_id), safe='')}"
        status, payload = self.request("PUT", path, {"fields": {"acl_tokens": {"assign": tokens}}})
        if status != 200:
            raise SearchUnavailable(f"Vespa refused the ACL update of {doc_id}: {status} {payload.get('message', '')}")

    def get(self, collection: str, doc_id: str) -> dict:
        path = (f"/document/v1/{NAMESPACE}/{DOCTYPE}/docid/{quote(document_id(collection, doc_id), safe='')}"
                "?format.tensors=short-value")
        status, payload = self.request("GET", path)
        if status != 200:
            raise SearchUnavailable(f"Document {doc_id} is not in collection {collection}")
        return payload["fields"]

    # Queries.

    def search(self, params: dict) -> dict:
        status, payload = self.request("POST", "/search/", params)
        root = payload.get("root", {})
        coverage = root.get("coverage", {})
        if status != 200 or root.get("errors") or coverage.get("degraded") or not coverage.get("full"):
            raise SearchUnavailable(f"Vespa returned an error or incomplete coverage: {root.get('errors')}")
        return payload

    @staticmethod
    def _where(collection: str, tokens: list[str] | None, cohort_below: int | None) -> str:
        where = f"collection contains {literal(collection)}"
        if tokens is not None:
            if not tokens:
                raise ValueError("A principal needs at least one token")
            where += " and (" + " or ".join(f"acl_tokens contains {literal(token)}" for token in tokens) + ")"
        if cohort_below is not None:
            if type(cohort_below) is not int or not 0 < cohort_below <= 1000:
                raise ValueError("Cohorts are permille values from 1 to 1000")
            if cohort_below < 1000:
                where += f" and cohort_bucket < {cohort_below}"
        return where

    def count(self, collection: str) -> int:
        payload = self.search({"yql": f"select * from {DOCTYPE} where {self._where(collection, None, None)} limit 0",
                               "timeout": f"{self.timeout}s"})
        return int(payload["root"]["fields"]["totalCount"])

    @staticmethod
    def hits(payload: dict, k: int) -> list[Hit]:
        hits = [Hit(child["fields"]["doc_id"], float(child["relevance"])) for child in payload["root"].get("children", [])]
        if len(hits) > k or any(not isfinite(hit.score) for hit in hits) or len({hit.doc_id for hit in hits}) != len(hits):
            raise SearchUnavailable("Invalid Vespa ranking")
        return hits

    def lexical(self, collection: str, query: str, k: int, *, profile: str = "bm25", tokens=None,
                cohort_below=None) -> list[Hit]:
        """BM25 over every document that holds a query term: OR semantics, an exhaustive top k."""
        yql = (f"select doc_id from {DOCTYPE} where {self._where(collection, tokens, cohort_below)} "
               'and ({grammar: "any", defaultIndex: "text"}userInput(@q))')
        return self.hits(self.search({"yql": yql, "q": query, "hits": k, "ranking": profile, "summary": "ids",
                                      "timeout": f"{self.timeout}s"}), k)

    def dense(self, collection: str, vector: list[float], k: int, *, approximate: bool = True, explore: int = 0,
              tokens=None, cohort_below=None, trace: bool = False) -> tuple[list[Hit], dict]:
        """Nearest neighbors by inner product. Returns the hits and, with `trace`, the raw response."""
        yql = (f"select doc_id from {DOCTYPE} where {self._where(collection, tokens, cohort_below)} and "
               f"({{targetHits:{k},approximate:{str(approximate).lower()},hnsw.exploreAdditionalHits:{explore}}}"
               "nearestNeighbor(embedding, q))")
        params = {"yql": yql, "input.query(q)": vector, "hits": k, "ranking": "dense", "summary": "ids",
                  "timeout": f"{self.timeout}s"}
        if trace:
            params.update({"trace.level": 6, "trace.explainLevel": 1})
        payload = self.search(params)
        return self.hits(payload, k), (payload if trace else {})


def wait_until(predicate, *, seconds: float, step: float = 0.01) -> float | None:
    """Seconds until `predicate()` holds, or None if it did not within the budget."""
    started = monotonic()
    while monotonic() - started < seconds:
        if predicate():
            return monotonic() - started
        sleep(step)
    return None
