"""Exact top-k over the eligible documents of a cohort, certified against the candidate search.

Definition. The exact score of a document for a query is the float64 sum of the products of
their canonical rows, reduced over the dimension axis by numpy for each pair separately.
The exact top k sorts eligible documents by descending score and breaks equal scores by
document ID in code point order.

Procedure. A flat inner-product index (FAISS, CPU or GPU) returns k' candidates with
approximate scores. The candidates are scored exactly and ranked. Accumulating in float32
over inputs that float32 represents exactly, an approximate score differs from the true dot
product by at most eps = gamma_d * max|q| * max|x|, with gamma_d = d u / (1 - d u) and
u = 2**-24 (Higham, Accuracy and Stability of Numerical Algorithms, 2nd ed., section 3.1).
Every document left out has an approximate score at most t, the lowest returned, so its
true score is at most t + eps. When the k-th exact score exceeds t + eps plus the float64
rounding of both sides, no document left out can enter the top k, ties included. Otherwise
the query is searched again with four times the candidates and, past the limit of the
searcher, scored exactly over every eligible document.

The bound holds only if the searcher accumulates in float32. Each run measures the largest
difference between approximate and exact scores over every candidate and stops if it
exceeds eps.
"""

from dataclasses import dataclass
import os

import numpy as np

from canonical import CanonicalRows


U32, U64 = 2.0 ** -24, 2.0 ** -53
COMPLETE, CERTIFIED, FULL_SCAN = 0, 1, 2
STATES = {COMPLETE: "complete", CERTIFIED: "certified", FULL_SCAN: "full-scan"}
GPU_MAX_K = 2048
SCAN_CHUNK = 32768


class OracleError(RuntimeError):
    """The exact ranking could not be established under the declared error bound."""


def gamma(n: int, u: float) -> float:
    if n * u >= 1:
        raise ValueError("Dimension too large for the error bound")
    return n * u / (1 - n * u)


def exact_scores(query: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """The exact score of one float64 query row against float64 document rows."""
    return (rows * query).sum(axis=1)


def rank(scores: np.ndarray, id_rank: np.ndarray, k: int) -> np.ndarray:
    """Positions of the top k by descending score, equal scores by ascending ID rank."""
    return np.lexsort((id_rank, -scores))[:k]


class FlatSearcher:
    """Candidate search over a subset of rows with a FAISS flat inner-product index."""

    def __init__(self, documents: CanonicalRows, eligible: np.ndarray, device: str = "cpu"):
        import faiss

        self.eligible, self.device = eligible, device
        d = documents.dimensions
        if device == "cpu":
            self.index, self.max_k = faiss.IndexFlatIP(d), None
        elif device.startswith("cuda"):
            if os.environ.get("NVIDIA_TF32_OVERRIDE", "0") != "0":
                raise OracleError("NVIDIA_TF32_OVERRIDE would let cuBLAS reduce precision")
            self.resources = faiss.StandardGpuResources()
            config = faiss.GpuIndexFlatConfig()
            config.device = int(device.partition(":")[2] or 0)
            # float16 rows are stored as float16: exact, and accumulated in float32 (PEDANTIC GEMM).
            config.useFloat16 = documents.dtype == "float16"
            config.use_cuvs = False
            self.index, self.max_k = faiss.GpuIndexFlatIP(self.resources, d, config), GPU_MAX_K
        else:
            raise ValueError(f"Unknown device {device}")
        for _, values in documents.chunks(eligible):
            self.index.add(values)
        if self.index.ntotal != eligible.size:
            raise OracleError("The flat index does not hold every eligible row")

    def limit(self) -> int:
        return self.eligible.size if self.max_k is None else min(self.eligible.size, self.max_k)

    def search(self, queries: np.ndarray, k: int, positions=None) -> tuple[np.ndarray, np.ndarray]:
        """Approximate scores and global row positions, best first."""
        scores, local = self.index.search(np.ascontiguousarray(queries, dtype=np.float32), k)
        if (local < 0).any():
            raise OracleError("The flat index returned fewer candidates than requested")
        return scores.astype(np.float64), self.eligible[local]


class BlockedSearcher:
    """Candidates for every query, computed one block of rows at a time.

    For corpora larger than device memory: each block is indexed, searched for all queries and
    released, and the best `k` of every block are merged. A document left out scores at most
    the k-th candidate of its block, which is at most the lowest merged candidate, so the
    certificate holds. `limit()` is the number kept, so a query that is not certified is scanned
    in full instead of searched again.
    """

    def __init__(self, documents: CanonicalRows, eligible: np.ndarray, queries: CanonicalRows, k: int,
                 device: str, block_rows: int, batch: int = 1024):
        if block_rows <= 0 or k <= 0:
            raise ValueError("Block size and candidates must be positive")
        self.eligible, self.k = eligible, min(k, eligible.size)
        nq = queries.n
        self.scores = np.full((nq, self.k), -np.inf, dtype=np.float64)
        self.rows = np.full((nq, self.k), -1, dtype=np.int64)
        for start in range(0, eligible.size, block_rows):
            block = eligible[start:start + block_rows]
            searcher = FlatSearcher(documents, block, device)
            wanted = min(self.k, block.size)
            for first in range(0, nq, batch):
                positions = np.arange(first, min(first + batch, nq), dtype=np.int64)
                found_scores, found = searcher.search(queries.take(positions, np.float32), wanted)
                scores = np.concatenate([self.scores[positions], found_scores], axis=1)
                rows = np.concatenate([self.rows[positions], found], axis=1)
                best = np.argsort(-scores, axis=1, kind="stable")[:, :self.k]
                self.scores[positions] = np.take_along_axis(scores, best, axis=1)
                self.rows[positions] = np.take_along_axis(rows, best, axis=1)
            del searcher
        if (self.rows < 0).any():
            raise OracleError("A query received fewer candidates than requested")

    def limit(self) -> int:
        return self.k

    def search(self, queries: np.ndarray, k: int, positions=None) -> tuple[np.ndarray, np.ndarray]:
        if positions is None or k > self.k:
            raise OracleError("Blocked candidates are read by query position, up to the number kept")
        return self.scores[positions, :k], self.rows[positions, :k]


@dataclass
class OracleResult:
    rows: np.ndarray        # (nq, k) global positions of the exact top k, -1 past the eligible count
    scores: np.ndarray      # (nq, k) exact scores, -inf past the eligible count
    states: np.ndarray      # (nq,) COMPLETE, CERTIFIED or FULL_SCAN
    candidates: np.ndarray  # (nq,) candidates examined; the eligible count for a full scan
    epsilon: float
    observed_max_error: float

    def state_counts(self) -> dict[str, int]:
        return {name: int((self.states == code).sum()) for code, name in STATES.items()}


def full_scan(query: np.ndarray, documents: CanonicalRows, eligible: np.ndarray, k: int):
    scores = np.empty(eligible.size, dtype=np.float64)
    for start in range(0, eligible.size, SCAN_CHUNK):
        part = eligible[start:start + SCAN_CHUNK]
        scores[start:start + part.size] = exact_scores(query, documents.take(part))
    if eligible.size > k:
        # Every document scoring at least the k-th value, so that ties are broken by ID.
        threshold = np.partition(scores, eligible.size - k)[eligible.size - k]
        keep = np.nonzero(scores >= threshold)[0]
    else:
        keep = np.arange(eligible.size)
    best = keep[rank(scores[keep], documents.id_rank[eligible[keep]], k)]
    return eligible[best], scores[best]


def exact_top_k(queries: CanonicalRows, documents: CanonicalRows, eligible: np.ndarray, k: int,
                searcher, *, initial: int, batch: int = 1024) -> OracleResult:
    """The exact top k of every query over `eligible`, sorted global row positions."""
    if queries.space != documents.space or queries.dimensions != documents.dimensions:
        raise OracleError("Queries and documents belong to different spaces")
    if k <= 0 or initial < k:
        raise ValueError("Candidates must cover at least k")
    eligible = np.asarray(eligible, dtype=np.int64)
    if eligible.size and (np.diff(eligible) <= 0).any():
        raise ValueError("Eligible rows must be sorted and distinct")
    nq, n = queries.n, eligible.size
    rows = np.full((nq, k), -1, dtype=np.int64)
    scores = np.full((nq, k), -np.inf, dtype=np.float64)
    states = np.zeros(nq, dtype=np.uint8)
    used = np.zeros(nq, dtype=np.int64)
    d = documents.dimensions
    # One pass over the eligible rows: at MS MARCO scale each pass reads every shard.
    norms = queries.max_norm() * (documents.max_norm(eligible) if n else 0.0)
    epsilon, slack = gamma(d, U32) * norms, 2 * gamma(d, U64) * norms
    observed = 0.0
    if n == 0:
        return OracleResult(rows, scores, states, used, epsilon, observed)
    limit = searcher.limit()
    for start in range(0, nq, batch):
        positions = np.arange(start, min(start + batch, nq), dtype=np.int64)
        values32 = queries.take(positions, np.float32)
        wanted = min(initial, limit)
        approximate, candidates = searcher.search(values32, wanted, positions)
        for offset, query_row in enumerate(positions):
            query = values32[offset].astype(np.float64)
            found_scores, found = approximate[offset], candidates[offset]
            while True:
                exact = exact_scores(query, documents.take(found))
                error = float(np.abs(exact - found_scores).max())
                if error > epsilon:
                    raise OracleError(f"Approximate score error {error:.3e} exceeds the bound {epsilon:.3e}: "
                                      "the searcher does not accumulate in float32")
                observed = max(observed, error)
                best = rank(exact, documents.id_rank[found], k)
                count = best.size
                if found.size == n:
                    state = COMPLETE
                    break
                if count == k and exact[best[-1]] > found_scores.min() + epsilon + slack:
                    state = CERTIFIED
                    break
                if found.size >= limit:
                    best_rows, best_scores = full_scan(query, documents, eligible, k)
                    rows[query_row, :best_rows.size], scores[query_row, :best_rows.size] = best_rows, best_scores
                    states[query_row], used[query_row] = FULL_SCAN, n
                    break
                wanted = min(found.size * 4, limit)
                found_scores, found = searcher.search(values32[offset:offset + 1], wanted,
                                                      positions[offset:offset + 1])
                found_scores, found = found_scores[0], found[0]
            if states[query_row] != FULL_SCAN:
                rows[query_row, :count] = found[best]
                scores[query_row, :count] = exact[best]
                states[query_row], used[query_row] = state, found.size
    return OracleResult(rows, scores, states, used, epsilon, observed)


# Sealed artifacts. The body is independent of the searcher and device that computed it;
# how it was computed (device, bound, observed error, states) goes to observation.json.

ARRAYS = {"rows": "<i8", "scores": "<f8"}


def write_oracle(directory, result: OracleResult, *, documents: CanonicalRows, queries: CanonicalRows,
                 permille: int, rule: str, eligible: np.ndarray, k: int, implementation: str,
                 observation: dict) -> dict:
    import json
    from hashlib import sha256

    from search_retrieval.experiment.manifests import seal
    from vector_store import _save_atomic, _write_atomic

    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, dtype in ARRAYS.items():
        values = np.ascontiguousarray(getattr(result, name), dtype=dtype)
        _save_atomic(directory / f"{name}.npy", values)
        hashes[name] = sha256(values.tobytes()).hexdigest()
    body = {
        "documents": documents.digest, "queries": queries.digest, "space": documents.space,
        "k": k, "queries_count": queries.n,
        "cohort": {"permille": permille, "rule": rule, "eligible": int(eligible.size),
                   "eligible_rows_sha256": sha256(np.ascontiguousarray(eligible, dtype="<i8").tobytes()).hexdigest()},
        "score": "float64 sum of products of canonical rows, reduced over the dimension axis",
        "tie_break": "document ID, code point order",
        # The code that defines the score, the ranking and the cohorts: another definition is another oracle.
        "implementation_sha256": implementation,
        "rows_sha256": hashes["rows"], "scores_sha256": hashes["scores"],
    }
    sealed = seal("exact-oracle", body)
    observation = {**observation, "epsilon": result.epsilon, "observed_max_error": result.observed_max_error,
                   "states": result.state_counts(),
                   "candidates": {"min": int(result.candidates.min()) if result.candidates.size else 0,
                                  "max": int(result.candidates.max()) if result.candidates.size else 0}}
    _write_atomic(directory / "observation.json", (json.dumps(observation, indent=2, sort_keys=True) + "\n").encode())
    _write_atomic(directory / "manifest.json", (json.dumps(sealed, indent=2) + "\n").encode())
    return sealed


def read_oracle(directory) -> tuple[dict, np.ndarray, np.ndarray]:
    """Verify a sealed oracle and return its body, rows and scores."""
    from hashlib import sha256

    from search_retrieval.experiment.manifests import ManifestError, parse_manifest

    try:
        body, _ = parse_manifest((directory / "manifest.json").read_bytes(), "exact-oracle")
    except (OSError, ManifestError) as error:
        raise OracleError(f"{directory}: {error}") from error
    arrays = {}
    for name, dtype in ARRAYS.items():
        values = np.load(directory / f"{name}.npy", allow_pickle=False)
        if values.dtype != np.dtype(dtype) or values.shape != (body["queries_count"], body["k"]) \
                or sha256(values.tobytes()).hexdigest() != body[f"{name}_sha256"]:
            raise OracleError(f"{directory}: {name}.npy does not match the oracle manifest")
        arrays[name] = values
    return body, arrays["rows"], arrays["scores"]
