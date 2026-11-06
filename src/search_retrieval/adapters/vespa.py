"""HTTP feed and search for the self-hosted witness application."""

import json
from math import isfinite
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen

from search_retrieval.ingestion.publication import Fragment, Work
from search_retrieval.query.eligible import Eligibility, Hit, SearchUnavailable


def _literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class VespaWitness:
    def __init__(self, url: str, *, visibility_seconds: float = 15):
        self.url = url.rstrip("/")
        self.visibility_seconds = visibility_seconds

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        request = Request(
            self.url + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (URLError, OSError, ValueError) as error:
            raise SearchUnavailable(f"Vespa request failed for {path}") from error

    def _search(self, yql: str, hits: int, timeout: float, **params) -> list[dict]:
        root = self._post(
            "/search/", {"yql": yql, "hits": hits, "timeout": f"{timeout}s", **params}, timeout
        )["root"]
        if root.get("errors") or not root.get("coverage", {}).get("full"):
            raise SearchUnavailable("Vespa returned incomplete coverage")
        return root.get("children", [])

    @staticmethod
    def _filter(tenant: str, generation: str) -> str:
        return f"tenant contains {_literal(tenant)} and generation contains {_literal(generation)}"

    def ensure_visible(self, work: Work, projection: str, fragments: tuple[Fragment, ...]) -> None:
        for fragment in fragments:
            self._post(
                f"/document/v1/lab/fragment/docid/{fragment.fragment_id}",
                {"fields": {
                    "tenant": work.tenant, "generation": work.generation,
                    "projection_id": projection, "fragment_id": fragment.fragment_id,
                    "body": fragment.text, "digest": fragment.sha256,
                }},
                self.visibility_seconds,
            )
        expected = {fragment.fragment_id: fragment.sha256 for fragment in fragments}
        yql = (
            "select fragment_id, digest from fragment where "
            f"{self._filter(work.tenant, work.generation)} and projection_id contains {_literal(projection)}"
        )
        deadline = monotonic() + self.visibility_seconds
        while (remaining := deadline - monotonic()) > 0:
            rows = self._search(yql, len(expected) + 1, remaining, ranking="unranked")
            visible = {row["fields"]["fragment_id"]: row["fields"]["digest"] for row in rows}
            if visible == expected and len(rows) == len(expected):
                return
            sleep(min(0.1, remaining))
        raise SearchUnavailable("Projection is not fully visible through Vespa search")

    def lexical(
        self, eligibility: Eligibility, query: str, k: int, timeout: float
    ) -> tuple[Hit, ...]:
        if not eligibility.projections:
            return ()
        allowed = ",".join(map(_literal, sorted(eligibility.projections)))
        yql = (
            "select projection_id, fragment_id, body from fragment where "
            f"{self._filter(eligibility.tenant, eligibility.generation)} "
            f"and projection_id in ({allowed}) and userInput(@terms)"
        )
        rows = self._search(yql, k, timeout, terms=query, ranking="lexical")
        hits = tuple(
            Hit(row["fields"]["projection_id"], row["fields"]["fragment_id"],
                row["fields"]["body"], row["relevance"])
            for row in rows
        )
        if len(hits) > k or any(not isfinite(hit.score) for hit in hits):
            raise SearchUnavailable("Invalid Vespa ranking")
        return hits
