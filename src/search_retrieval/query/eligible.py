"""Authorize projected identities before ranking and revalidate before use."""

from dataclasses import dataclass
from time import monotonic
from typing import Protocol


class SearchUnavailable(Exception):
    """The authorized result cannot be established with complete coverage."""


@dataclass(frozen=True, slots=True)
class Eligibility:
    tenant: str
    generation: str
    projections: frozenset[str]


@dataclass(frozen=True, slots=True)
class Hit:
    projection_id: str
    fragment_id: str
    text: str
    score: float


@dataclass(frozen=True, slots=True)
class SearchResult:
    eligibility: Eligibility
    hits: tuple[Hit, ...]


class Authority(Protocol):
    def eligibility(self, tenant: str) -> Eligibility: ...


class Index(Protocol):
    def lexical(
        self, eligibility: Eligibility, query: str, k: int, timeout: float
    ) -> tuple[Hit, ...]: ...


class EligibleSearch:
    """Tenant is provided by the authenticated caller of the laboratory profile."""

    def __init__(self, authority: Authority, index: Index):
        self.authority = authority
        self.index = index

    def search(self, tenant: str, query: str, *, k: int = 10, timeout: float = 5) -> SearchResult:
        if not tenant.strip() or not query.strip() or not 1 <= k <= 100 or timeout <= 0:
            raise ValueError("Invalid tenant, query, depth or deadline")
        deadline = monotonic() + timeout
        for _ in range(2):
            before = self.authority.eligibility(tenant)
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            hits = self.index.lexical(before, query, k, remaining) if before.projections else ()
            if any(hit.projection_id not in before.projections for hit in hits):
                raise SearchUnavailable("Index returned a projection outside its filter")
            if self.authority.eligibility(tenant) == before and monotonic() < deadline:
                return SearchResult(before, hits)
        raise SearchUnavailable("Eligibility changed or search deadline expired")

    def context(self, tenant: str, result: SearchResult) -> tuple[Hit, ...]:
        """Revalidate a live or cached result before exposing its evidence."""
        current = self.authority.eligibility(tenant)
        if (tenant, current.generation) != (result.eligibility.tenant, result.eligibility.generation):
            return ()
        return tuple(hit for hit in result.hits if hit.projection_id in current.projections)
