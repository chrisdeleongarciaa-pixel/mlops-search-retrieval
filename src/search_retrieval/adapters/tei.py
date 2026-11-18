"""Online query encoding through Text Embeddings Inference (TEI).

TEI applies the model's pooling and dense layers; the prompt is the client's. The client renders
the query template of the registered space, so the online path and the batch encoder build the
same input string. Every returned row must be finite, of the space's dimension and unit length.
"""

from dataclasses import dataclass
import http.client
import json
from math import fsum, isfinite, sqrt
import threading
from urllib.parse import urlsplit

from search_retrieval.adapters.space_encoder import render
from search_retrieval.query.eligible import SearchUnavailable


NORM_TOLERANCE = 1e-4


@dataclass(frozen=True)
class TeiSpace:
    query_template: str
    dimensions: int

    @classmethod
    def from_body(cls, body: dict) -> "TeiSpace":
        if not body["normalize"]:
            raise ValueError("The online path serves normalized spaces only")
        return cls(body["query_template"], body["dim"])


class TeiEncoder:
    def __init__(self, url: str, space: TeiSpace, *, timeout: float = 5.0):
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("Expected an http:// TEI endpoint")
        self.host, self.port, self.space, self.timeout = parts.hostname, parts.port or 80, space, timeout
        self._local = threading.local()

    def _connection(self) -> http.client.HTTPConnection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return connection

    def encode_queries(self, queries: list[str]) -> list[list[float]]:
        inputs = [render(self.space.query_template, "", query) for query in queries]
        body = json.dumps({"inputs": inputs, "normalize": True, "truncate": False}).encode("utf-8")
        for attempt in (1, 2):
            connection = self._connection()
            try:
                connection.request("POST", "/embed", body, {"Content-Type": "application/json"})
                response = connection.getresponse()
                payload = response.read()
                break
            except (http.client.HTTPException, OSError) as error:
                connection.close()
                self._local.connection = None
                if attempt == 2:
                    raise SearchUnavailable("TEI is unreachable") from error
        if response.status != 200:
            raise SearchUnavailable(f"TEI answered {response.status}")
        rows = json.loads(payload)
        if not isinstance(rows, list) or len(rows) != len(queries):
            raise SearchUnavailable("TEI returned another number of rows")
        for row in rows:
            if (not isinstance(row, list) or len(row) != self.space.dimensions
                    or not all(isinstance(value, (int, float)) and isfinite(value) for value in row)
                    or abs(sqrt(fsum(value * value for value in row)) - 1) > NORM_TOLERANCE):
                raise SearchUnavailable("TEI returned a row of the wrong shape, a non-finite value or a non-unit row")
        return [[float(value) for value in row] for row in rows]
