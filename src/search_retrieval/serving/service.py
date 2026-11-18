"""One retrieval request: encode, search the engine, fuse, revalidate, answer.

Eligibility is applied twice. The engine filters on ACL tokens before its top k (`public`, plus
the principal for documents granted to it). The authority then revalidates the returned
documents and removes any private document the principal may no longer read, so a revocation
takes effect at delivery as soon as it is committed, before the engine's partial update is
visible. An unreachable authority fails the request.
"""

from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Protocol

from search_retrieval.adapters.vespa_passages import VespaPassages
from search_retrieval.query.eligible import SearchUnavailable
from search_retrieval.query.ranking import Ranked, convex, fuse


ROUTES = ("bm25", "dense", "hybrid")
PUBLIC = "public"


class Authority(Protocol):
    def tokens(self, principal: str | None) -> list[str]: ...
    def denied(self, principal: str | None, doc_ids: list[str]) -> set[str]: ...


class PublicAuthority:
    """Every document is public: the engine filter is `public` and nothing is revoked."""

    def tokens(self, principal):
        return [PUBLIC]

    def denied(self, principal, doc_ids):
        return set()


class Encoder(Protocol):
    def encode_queries(self, queries: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class Fusion:
    kind: str = "rrf"
    alpha: float = 0.5

    def __post_init__(self):
        if self.kind not in ("rrf", "convex") or not 0.0 <= self.alpha <= 1.0:
            raise ValueError("Fusion is rrf, or convex with alpha in [0, 1]")


@dataclass
class Answer:
    hits: list[tuple[str, float]]
    stages_ms: dict[str, float] = field(default_factory=dict)
    removed_by_authority: int = 0


def _timed(stages: dict, name: str, operation, *args, **kwargs):
    started = perf_counter_ns()
    try:
        return operation(*args, **kwargs)
    finally:
        stages[name] = (perf_counter_ns() - started) / 1e6


class RetrievalService:
    def __init__(self, engine: VespaPassages, encoder: Encoder | None, authority: Authority, collection: str, *,
                 fusion: Fusion = Fusion(), branch_depth: int = 100, explore: int = 0,
                 lexical_profile: str = "bm25"):
        if branch_depth <= 0 or explore < 0:
            raise ValueError("Branch depth must be positive and exploration not negative")
        self.engine, self.encoder, self.authority, self.collection = engine, encoder, authority, collection
        self.fusion, self.branch_depth, self.explore, self.lexical_profile = fusion, branch_depth, explore, lexical_profile

    def search(self, query: str, *, route: str = "hybrid", k: int = 10, principal: str | None = None) -> Answer:
        if route not in ROUTES or not query.strip() or not 1 <= k <= self.branch_depth:
            raise ValueError("Unknown route, empty query or k outside 1..branch depth")
        if route != "bm25" and self.encoder is None:
            raise SearchUnavailable("This service has no query encoder")
        stages: dict[str, float] = {}
        started = perf_counter_ns()
        tokens = self.authority.tokens(principal)
        depth = self.branch_depth if route == "hybrid" else k
        lexical = dense = None
        if route in ("bm25", "hybrid"):
            lexical = _timed(stages, "engine_lexical", self.engine.lexical, self.collection, query, depth,
                             profile=self.lexical_profile, tokens=tokens)
        if route in ("dense", "hybrid"):
            vector, = _timed(stages, "encode", self.encoder.encode_queries, [query])
            dense, _ = _timed(stages, "engine_dense", self.engine.dense, self.collection, vector, depth,
                              approximate=True, explore=self.explore, tokens=tokens)
        if route == "hybrid":
            branches = ([Ranked(hit.doc_id, hit.score) for hit in lexical], [Ranked(hit.doc_id, hit.score) for hit in dense])
            fused = _timed(stages, "fusion", lambda: (fuse(*branches, depth=depth) if self.fusion.kind == "rrf"
                                                      else convex(*branches, alpha=self.fusion.alpha, depth=depth)))
            ranked = [(hit.id, hit.score) for hit in fused.fused]
        else:
            ranked = [(hit.doc_id, hit.score) for hit in (lexical if route == "bm25" else dense)]
        ids = [doc for doc, _ in ranked]
        denied = _timed(stages, "revalidation", self.authority.denied, principal, ids)
        kept = [(doc, score) for doc, score in ranked if doc not in denied][:k]
        stages["service_total"] = (perf_counter_ns() - started) / 1e6
        return Answer(kept, stages, len([doc for doc in ids if doc in denied]))
