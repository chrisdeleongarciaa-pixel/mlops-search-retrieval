"""Authorize projected identities before ranking and revalidate before use."""

from dataclasses import dataclass
from time import monotonic, perf_counter_ns
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


def timed(sample, stage, operation, *args, **kwargs):
    if sample is None:
        return operation(*args, **kwargs)
    start = perf_counter_ns()
    try:
        return operation(*args, **kwargs)
    finally:
        sample[stage] = (perf_counter_ns() - start) / 1_000_000


class EligibleSearch:
    """Tenant is provided by the authenticated caller of the laboratory profile."""

    def __init__(self, authority: Authority, index: Index):
        self.authority = authority
        self.index = index

    def search(self, tenant: str, query: str, *, k: int = 10, timeout: float = 5, attempts=None) -> SearchResult:
        return self._search(tenant, query, k, timeout, attempts, self._lexical)

    def _lexical(self, eligibility, query, k, timeout, sample):
        return timed(sample, "vespa_lexical_ms", self.index.lexical, eligibility, query, k, timeout)

    def _check_snapshot(self, eligibility):
        pass

    def _search(self, tenant, query, k, timeout, attempts, rank):
        if not tenant.strip() or not query.strip() or not 1 <= k <= 100 or timeout <= 0:
            raise ValueError("Invalid tenant, query, depth or deadline")
        deadline = monotonic() + timeout
        for number in (1, 2):
            sample = None
            if attempts is not None:
                sample = {"attempt": number}
                attempts.append(sample)
            before = timed(sample, "eligibility_ms", self.authority.eligibility, tenant)
            self._check_snapshot(before)
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            hits = rank(before, query, k, remaining, sample) if before.projections else ()
            if any(hit.projection_id not in before.projections for hit in hits):
                raise SearchUnavailable("Index returned a projection outside its filter")
            after = timed(sample, "revalidation_ms", self.authority.eligibility, tenant)
            if sample is not None:
                sample["snapshot_stable"] = after == before
            if after == before and monotonic() < deadline:
                return SearchResult(before, hits)
        raise SearchUnavailable("Eligibility changed or search deadline expired")

    def context(self, tenant: str, result: SearchResult) -> tuple[Hit, ...]:
        """Revalidate a live or cached result before exposing its evidence."""
        current = self.authority.eligibility(tenant)
        if (tenant, current.generation) != (result.eligibility.tenant, result.eligibility.generation):
            return ()
        return tuple(hit for hit in result.hits if hit.projection_id in current.projections)
