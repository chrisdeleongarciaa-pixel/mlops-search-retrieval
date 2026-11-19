"""Independent lexical/dense ranks, exact cosine reference and RRF."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum, hypot, isfinite


@dataclass(frozen=True, slots=True)
class Ranked:
    id: str
    score: float


@dataclass(frozen=True, slots=True)
class HybridRun:
    lexical: tuple[Ranked, ...]
    dense: tuple[Ranked, ...]
    fused: tuple[Ranked, ...]


def fuse(lexical: Sequence[Ranked], dense: Sequence[Ranked], *, depth: int = 100) -> HybridRun:
    """Consume two complete branch rankings for the same eligible snapshot."""
    if depth <= 0:
        raise ValueError("Invalid branch depth")
    branches = []
    contributions: dict[str, list[float]] = {}
    for branch in (lexical, dense):
        seen = set()
        for hit in branch:
            if not hit.id or hit.id in seen or not isfinite(hit.score):
                raise ValueError("Invalid or duplicate branch result")
            seen.add(hit.id)
        ordered = tuple(sorted(branch, key=lambda hit: (-hit.score, hit.id))[:depth])
        branches.append(ordered)
        for rank, hit in enumerate(ordered, 1):
            contributions.setdefault(hit.id, []).append(1 / (60 + rank))
    fused = sorted(
        (Ranked(item, fsum(scores)) for item, scores in contributions.items()),
        key=lambda hit: (-hit.score, hit.id),
    )
    return HybridRun(branches[0], branches[1], tuple(fused))


def exact_cosine(
    query: Sequence[float], vectors: Mapping[str, Sequence[float]],
    eligible: frozenset[str], *, k: int,
) -> tuple[Ranked, ...]:
    """Rank an explicitly eligible snapshot. Missing vectors invalidate coverage."""
    norm = hypot(*query)
    if k <= 0 or not isfinite(norm) or norm == 0:
        raise ValueError("Invalid query or depth")
    hits = []
    for item in eligible:
        if item not in vectors:
            raise ValueError(f"Missing vector: {item}")
        vector = vectors[item]
        other = hypot(*vector)
        if len(vector) != len(query) or not isfinite(other) or other == 0:
            raise ValueError(f"Invalid vector: {item}")
        score = fsum((x / norm) * (y / other) for x, y in zip(query, vector))
        hits.append(Ranked(item, score))
    return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.id))[:k])


def _branch(hits: Sequence[Ranked], depth: int) -> tuple[Ranked, ...]:
    seen = set()
    for hit in hits:
        if not hit.id or hit.id in seen or not isfinite(hit.score):
            raise ValueError("Invalid or duplicate branch result")
        seen.add(hit.id)
    return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.id))[:depth])


def min_max(hits: Sequence[Ranked]) -> dict[str, float]:
    """Scores scaled to [0, 1] within one query's list. A list of equal scores maps to 1."""
    if not hits:
        return {}
    low, high = min(hit.score for hit in hits), max(hit.score for hit in hits)
    span = high - low
    return {hit.id: (hit.score - low) / span if span > 0 else 1.0 for hit in hits}


def convex(lexical: Sequence[Ranked], dense: Sequence[Ranked], *, alpha: float, depth: int = 1000) -> HybridRun:
    """alpha * dense + (1 - alpha) * lexical over min-max scaled scores (Bruch, Gai and Ingber, TOIS 2023).

    A document missing from one branch takes 0, the bottom of that branch's scaled range.
    Ties are broken by ID.
    """
    if not 0.0 <= alpha <= 1.0 or depth <= 0:
        raise ValueError("alpha must lie in [0, 1] and depth must be positive")
    lexical_branch, dense_branch = _branch(lexical, depth), _branch(dense, depth)
    lexical_scaled, dense_scaled = min_max(lexical_branch), min_max(dense_branch)
    fused = sorted(
        (Ranked(item, alpha * dense_scaled.get(item, 0.0) + (1 - alpha) * lexical_scaled.get(item, 0.0))
         for item in lexical_scaled.keys() | dense_scaled.keys()),
        key=lambda hit: (-hit.score, hit.id),
    )
    return HybridRun(lexical_branch, dense_branch, tuple(fused))
