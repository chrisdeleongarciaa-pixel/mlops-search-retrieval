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
