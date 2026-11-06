"""HTTP feed and search for the self-hosted witness application."""

import json
from math import hypot, isfinite
from time import monotonic, sleep
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from search_retrieval.ingestion.publication import Fragment, Work
from search_retrieval.query.eligible import Eligibility, Hit, SearchUnavailable


def _literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class VespaWitness:
    def __init__(self, url: str, *, visibility_seconds: float = 15, encoder=None):
        self.url = url.rstrip("/")
        self.visibility_seconds = visibility_seconds
        self.encoder = encoder

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

    def _search_response(self, yql: str, hits: int, timeout: float, **params) -> dict:
        response = self._post(
            "/search/", {"yql": yql, "hits": hits, "timeout": f"{timeout}s", **params}, timeout
        )
        root = response.get("root", {})
        coverage = root.get("coverage", {})
        if root.get("errors") or coverage.get("degraded") or not coverage.get("full"):
            raise SearchUnavailable("Vespa returned incomplete coverage")
        return response

    def _search(self, yql: str, hits: int, timeout: float, **params) -> list[dict]:
        return self._search_response(yql, hits, timeout, **params)["root"].get("children", [])

    @staticmethod
    def _filter(tenant: str, generation: str) -> str:
        return f"tenant contains {_literal(tenant)} and generation contains {_literal(generation)}"

    def _allowed(self, eligibility: Eligibility) -> str:
        allowed = ",".join(map(_literal, sorted(eligibility.projections)))
        return f"{self._filter(eligibility.tenant, eligibility.generation)} and projection_id in ({allowed})"

    def feed(self, document_id: str, fields: dict) -> None:
        self._post(
            f"/document/v1/lab/fragment/docid/{quote(document_id, safe='')}",
            {"fields": fields}, self.visibility_seconds,
        )

    def ensure_visible(self, work: Work, projection: str, fragments: tuple[Fragment, ...]) -> None:
        encoder = self.encoder
        vectors = encoder.encode([fragment.text for fragment in fragments], role="passage") if encoder else [None] * len(fragments)
        for fragment, vector in zip(fragments, vectors, strict=True):
            fields = {
                "tenant": work.tenant, "generation": work.generation,
                "projection_id": projection, "fragment_id": fragment.fragment_id,
                "body": fragment.text, "digest": fragment.sha256,
            }
            if encoder:
                _validate_vector(vector)
                fields.update(vector_space=encoder.space.id, embedding={"values": vector})
            self.feed(fragment.fragment_id, fields)
        expected = {fragment.fragment_id: fragment.sha256 for fragment in fragments}
        yql = (
            "select fragment_id, digest, vector_space, embedding from fragment where "
            f"{self._filter(work.tenant, work.generation)} and projection_id contains {_literal(projection)}"
        )
        deadline = monotonic() + self.visibility_seconds
        while (remaining := deadline - monotonic()) > 0:
            rows = self._search(yql, len(expected) + 1, remaining, ranking="unranked")
            visible = {row["fields"]["fragment_id"]: row["fields"]["digest"] for row in rows}
            if visible == expected and len(rows) == len(expected) and (
                not encoder or all(row["fields"].get("vector_space") == encoder.space.id
                                   and row["fields"].get("embedding") for row in rows)
            ):
                return
            sleep(min(0.1, remaining))
        raise SearchUnavailable("Projection is not fully visible through Vespa search")

    def lexical(
        self, eligibility: Eligibility, query: str, k: int, timeout: float
    ) -> tuple[Hit, ...]:
        if not eligibility.projections:
            return ()
        yql = (
            "select projection_id, fragment_id, body from fragment where "
            f"{self._allowed(eligibility)} and userInput(@terms)"
        )
        rows = self._search(yql, k, timeout, terms=query, ranking="lexical")
        return self._hits(rows, k)

    @staticmethod
    def _hits(rows: list[dict], k: int) -> tuple[Hit, ...]:
        hits = tuple(
            Hit(row["fields"]["projection_id"], row["fields"]["fragment_id"],
                row["fields"]["body"], row["relevance"])
            for row in rows
        )
        if len(hits) > k or any(not isfinite(hit.score) for hit in hits):
            raise SearchUnavailable("Invalid Vespa ranking")
        return hits

    def dense_response(
        self, eligibility: Eligibility, vector: list[float], space: str, timeout: float, *,
        approximate: bool, explore: int = 0, k: int = 100, trace: bool = False,
        whole_generation: bool = False, include_body: bool = True, study_bucket_lt: int | None = None,
    ) -> dict:
        """Search a verified vector snapshot. Whole-generation mode is for immutable benchmarks."""
        _validate_vector(vector)
        if not 1 <= k <= 100 or explore < 0 or not space:
            raise ValueError("Invalid vector search configuration")
        if study_bucket_lt is not None and (not whole_generation or type(study_bucket_lt) is not int
                                           or not 1 <= study_bucket_lt <= 100):
            raise ValueError("The bucket filter requires a validated immutable study generation")
        if not eligibility.projections:
            return {"root": {"children": []}}
        filters = (self._filter(eligibility.tenant, eligibility.generation)
                   if whole_generation else self._allowed(eligibility))
        if study_bucket_lt is not None:
            filters += f" and study_bucket < {study_bucket_lt}"
        fields = "projection_id, fragment_id, body" if include_body else "projection_id, fragment_id"
        yql = (
            f"select {fields} from fragment where "
            f"{filters} and vector_space contains {_literal(space)} and "
            f"({{totalTargetHits:{k},approximate:{str(approximate).lower()},"
            f"hnsw.exploreAdditionalHits:{explore}}}nearestNeighbor(embedding,q))"
        )
        options = {"input.query(q)": vector, "ranking": "dense"}
        if trace:
            options.update({"trace.level": 6, "trace.explainLevel": 1})
        return self._search_response(yql, k, timeout, **options)

    def verify_vector_coverage(self, eligibility: Eligibility, space: str, timeout: float) -> int:
        # The mutable witness is intentionally bounded. An index miss must not look like a valid empty list.
        yql = f"select projection_id, fragment_id, vector_space, embedding from fragment where {self._allowed(eligibility)}"
        response = self._search_response(yql, 400, timeout, ranking="unranked")
        root = response["root"]
        rows = [row["fields"] for row in root.get("children", [])]
        fragment_ids = {row.get("fragment_id") for row in rows}
        if (root.get("fields", {}).get("totalCount") != len(rows)
                or {row.get("projection_id") for row in rows} != eligibility.projections
                or len(fragment_ids) != len(rows)
                or (eligibility.fragments is not None and fragment_ids != eligibility.fragments)
                or any(row.get("vector_space") != space or not row.get("embedding") for row in rows)):
            raise SearchUnavailable("Missing or incompatible vector in the eligible snapshot")
        return len(rows)

    def dense(self, eligibility, vector, space, timeout, *, approximate, explore, k):
        response = self.dense_response(eligibility, vector, space, timeout, approximate=approximate, explore=explore, k=k)
        hits = self._hits(response["root"].get("children", []), k)
        if eligibility.projections and not hits:
            raise SearchUnavailable("Vector ranking returned no eligible candidates")
        if len({hit.fragment_id for hit in hits}) != len(hits):
            raise SearchUnavailable("Vector ranking returned duplicate fragments")
        return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.fragment_id)))


def _validate_vector(vector):
    if len(vector) != 768 or not all(map(isfinite, vector)) or abs(hypot(*vector) - 1) > 1e-5:
        raise ValueError("Vespa requires a finite normalized 768-dimensional vector")
