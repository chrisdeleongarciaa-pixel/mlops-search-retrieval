"""Exact oracle and selectivity cohorts.

The reference ranking sorts every eligible document by `math.fsum` of the products, a
correctly rounded sum computed without numpy or FAISS, and breaks ties by ID. Cohort buckets
are checked against `sha256sum` over literal bytes.
"""

from math import fsum
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from search_retrieval.contracts.cohorts import COHORT_RULE, cohort_bucket, cohort_buckets, cohort_mask
from search_retrieval.contracts.identity import SpaceKey
from canonical import CanonicalRows
from oracle import (
    CERTIFIED, COMPLETE, FULL_SCAN, BlockedSearcher, FlatSearcher, OracleError, exact_scores, exact_top_k, gamma, read_oracle,
    write_oracle, U32,
)
from vector_store import Identity, VectorSetWriter, VectorStoreError


A, B = "a" * 64, "b" * 64


def space(dtype="float32", dimensions=16) -> SpaceKey:
    return SpaceKey(encoder="synthetic/test", revision="r1", files={"weights": A}, query_template="{text}",
                    document_template="{text}", pooling="none", normalize=True, max_tokens=8, dtype=dtype,
                    dimensions=dimensions, mrl_dimensions=dimensions, quantization="none")


def unit(rows: np.ndarray) -> np.ndarray:
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


OPENED: list[CanonicalRows] = []


def close_opened():
    while OPENED:
        OPENED.pop().close()


def write(directory: Path, ids, rows, *, role="document", key=None, shard_rows=7):
    key = key or space()
    identity = Identity(key, role, {"corpus": A, "transform": B} if role == "document" else {"queries": A})
    writer = VectorSetWriter(directory, identity, shard_rows=shard_rows,
                             parity_every=1 if key.dtype == "float16" else None)
    for start in range(0, len(ids), shard_rows):
        writer.append(list(ids[start:start + shard_rows]), rows[start:start + shard_rows], 0)
    writer.finish(len(ids))
    OPENED.append(CanonicalRows(directory))
    return OPENED[-1]


def reference(queries: CanonicalRows, documents: CanonicalRows, eligible, k):
    """Independent exact top k: correctly rounded sums, then descending score and ascending ID."""
    q = queries.take(np.arange(queries.n))
    x = documents.take(np.asarray(eligible, dtype=np.int64))
    result = []
    for row in q:
        scored = [(-fsum(float(a) * float(b) for a, b in zip(row, doc)), documents.ids[position], position)
                  for doc, position in zip(x, eligible)]
        result.append([(position, -negative) for negative, _, position in sorted(scored)[:k]])
    return result


class CohortTests(unittest.TestCase):
    def test_buckets_match_an_external_hash(self):
        # printf 'cohort\0d001' | sha256sum, first 16 hex digits, mod 1000
        self.assertEqual([cohort_bucket("d001"), cohort_bucket("query-7"), cohort_bucket("é")], [223, 515, 438])
        self.assertIn("mod 1000", COHORT_RULE)

    def test_the_cohort_threshold_is_strict(self):
        # d001 has bucket 223: outside the cohort of 223 permille, inside the cohort of 224.
        buckets = cohort_buckets(["d001"])
        self.assertEqual([bool(cohort_mask(buckets, permille)[0]) for permille in (223, 224)], [False, True])

    def test_cohorts_are_nested_and_close_to_their_fraction(self):
        ids = [f"doc-{index}" for index in range(20000)]
        buckets = cohort_buckets(ids)
        masks = {permille: cohort_mask(buckets, permille) for permille in (1000, 500, 100, 10, 1)}
        self.assertTrue(masks[1000].all())
        for small, large in ((1, 10), (10, 100), (100, 500), (500, 1000)):
            self.assertFalse((masks[small] & ~masks[large]).any())
        for permille in (500, 100, 10):
            self.assertAlmostEqual(masks[permille].mean(), permille / 1000, delta=4 * (permille / 1000 / 20000) ** 0.5 + 0.002)
        for permille in (0, 1001, 0.5):
            with self.subTest(permille=permille), self.assertRaises(ValueError):
                cohort_mask(buckets, permille)


class OracleTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(close_opened)
        self.root = Path(directory.name)
        rng = np.random.default_rng(5)
        # IDs out of code point order, so position order and ID order disagree.
        self.ids = [f"d{(index * 37) % 101:03d}" for index in range(101)]
        rows = unit(rng.standard_normal((101, 16)))
        # Three exact duplicates in different shards, and a near tie.
        rows[[20, 55, 90]] = rows[3]
        self.documents = write(self.root / "documents", self.ids, rows)
        self.queries = write(self.root / "queries", [f"q{index}" for index in range(9)],
                             np.vstack([rows[3:4], unit(rng.standard_normal((8, 16)))]), role="query")

    def check(self, result, eligible, k):
        for query, expected in enumerate(reference(self.queries, self.documents, eligible, k)):
            count = len(expected)
            self.assertEqual(result.rows[query, :count].tolist(), [position for position, _ in expected])
            np.testing.assert_allclose(result.scores[query, :count], [score for _, score in expected], atol=1e-12, rtol=0)
            self.assertTrue((result.rows[query, count:] == -1).all())
            self.assertTrue(np.isneginf(result.scores[query, count:]).all())

    def test_matches_the_independent_ranking_and_breaks_ties_by_id(self):
        eligible = np.arange(101)
        result = exact_top_k(self.queries, self.documents, eligible, 10, FlatSearcher(self.documents, eligible), initial=20)
        self.check(result, eligible, 10)
        tied = sorted([3, 20, 55, 90], key=self.ids.__getitem__)
        self.assertEqual(result.rows[0, :4].tolist(), tied)
        self.assertEqual(len(set(result.scores[0, :4].tolist())), 1)
        self.assertTrue((result.states == CERTIFIED).any())
        self.assertGreater(result.epsilon, 0)
        self.assertLessEqual(result.observed_max_error, result.epsilon)

    def test_identical_rows_score_identically_wherever_they_are_stored(self):
        query = self.queries.take(np.array([1]))[0]
        scores = exact_scores(query, self.documents.take(np.array([3, 20, 55, 90])))
        self.assertEqual(len(set(scores.tolist())), 1)

    def test_cohort_subsets_only_return_eligible_rows(self):
        buckets = cohort_buckets(self.documents.ids)
        for permille in (500, 100):
            eligible = np.nonzero(cohort_mask(buckets, permille))[0]
            result = exact_top_k(self.queries, self.documents, eligible, 10,
                                 FlatSearcher(self.documents, eligible), initial=12)
            self.check(result, eligible, 10)
            valid = result.rows[result.rows >= 0]
            self.assertTrue(np.isin(valid, eligible).all())

    def test_a_cohort_smaller_than_k_or_empty_is_complete(self):
        eligible = np.array([2, 40, 77])
        result = exact_top_k(self.queries, self.documents, eligible, 10, FlatSearcher(self.documents, eligible), initial=10)
        self.check(result, eligible, 10)
        self.assertTrue((result.states == COMPLETE).all())
        empty = exact_top_k(self.queries, self.documents, np.array([], dtype=np.int64), 10, None, initial=10)
        self.assertTrue((empty.rows == -1).all())

    def test_ties_at_the_boundary_force_more_candidates_then_a_full_scan(self):
        rng = np.random.default_rng(9)
        rows = unit(rng.standard_normal((60, 16)))
        rows[10:50] = rows[0]                      # forty-one identical rows
        ids = [f"t{(index * 7) % 61:02d}" for index in range(60)]
        documents = write(self.root / "ties", ids, rows)
        queries = write(self.root / "tie-queries", ["q0"], rows[0:1], role="query")
        eligible = np.arange(60)
        result = exact_top_k(queries, documents, eligible, 5, FlatSearcher(documents, eligible), initial=5)
        self.assertEqual(result.states.tolist(), [COMPLETE])  # retried until every row was a candidate
        self.assertEqual(result.candidates.tolist(), [60])

        class Limited(FlatSearcher):
            def limit(self):
                return 20

        limited = exact_top_k(queries, documents, eligible, 5, Limited(documents, eligible), initial=5)
        self.assertEqual(limited.states.tolist(), [FULL_SCAN])
        self.assertEqual(limited.rows.tolist(), result.rows.tolist())
        for query, expected in enumerate(reference(queries, documents, eligible, 5)):
            self.assertEqual(limited.rows[query].tolist(), [position for position, _ in expected])

    def test_blocked_candidates_give_the_same_oracle(self):
        eligible = np.arange(101)
        whole = exact_top_k(self.queries, self.documents, eligible, 10, FlatSearcher(self.documents, eligible),
                            initial=20)
        for block_rows in (7, 30, 101):
            blocked = exact_top_k(self.queries, self.documents, eligible, 10,
                                  BlockedSearcher(self.documents, eligible, self.queries, 20, "cpu", block_rows),
                                  initial=20)
            with self.subTest(block_rows=block_rows):
                np.testing.assert_array_equal(blocked.rows, whole.rows)
                np.testing.assert_array_equal(blocked.scores, whole.scores)
                self.check(blocked, eligible, 10)

    def test_blocked_ties_beyond_the_candidates_fall_back_to_a_full_scan(self):
        rng = np.random.default_rng(9)
        rows = unit(rng.standard_normal((60, 16)))
        rows[10:50] = rows[0]
        documents = write(self.root / "blocked-ties", [f"t{(index * 7) % 61:02d}" for index in range(60)], rows)
        queries = write(self.root / "blocked-tie-queries", ["q0"], rows[0:1], role="query")
        eligible = np.arange(60)
        result = exact_top_k(queries, documents, eligible, 5, BlockedSearcher(documents, eligible, queries, 8, "cpu", 25),
                             initial=8)
        self.assertEqual(result.states.tolist(), [FULL_SCAN])
        self.assertEqual(result.rows[0].tolist(), [position for position, _ in reference(queries, documents, eligible, 5)[0]])

    def test_a_searcher_that_breaks_the_error_bound_stops_the_oracle(self):
        eligible = np.arange(101)
        searcher = FlatSearcher(self.documents, eligible)

        class Lossy:
            def limit(self):
                return searcher.limit()

            def search(self, queries, k, positions=None):
                scores, rows = searcher.search(queries, k)
                return scores + 1e-3, rows

        with self.assertRaisesRegex(OracleError, "exceeds the bound"):
            exact_top_k(self.queries, self.documents, eligible, 10, Lossy(), initial=20)

    def test_float16_rows_are_searched_without_loss(self):
        rng = np.random.default_rng(3)
        rows = unit(rng.standard_normal((50, 16)))
        documents = write(self.root / "half", [f"h{index:02d}" for index in range(50)], rows, key=space("float16"))
        queries = write(self.root / "half-queries", ["q0", "q1"], unit(rng.standard_normal((2, 16))), role="query",
                        key=space("float16"))
        eligible = np.arange(50)
        result = exact_top_k(queries, documents, eligible, 10, FlatSearcher(documents, eligible), initial=12)
        self.assertEqual(result.observed_max_error <= gamma(16, U32) * 1.01, True)
        for query, expected in enumerate(reference(queries, documents, eligible, 10)):
            self.assertEqual(result.rows[query].tolist(), [position for position, _ in expected])

    def test_queries_of_another_space_are_rejected(self):
        other = write(self.root / "other", ["q"], unit(np.ones((1, 8))), role="query", key=space(dimensions=8))
        with self.assertRaisesRegex(OracleError, "different spaces"):
            exact_top_k(other, self.documents, np.arange(101), 10, None, initial=10)

    def test_sealed_oracle_round_trips_and_detects_changes(self):
        eligible = np.arange(101)
        result = exact_top_k(self.queries, self.documents, eligible, 10, FlatSearcher(self.documents, eligible), initial=20)
        target = self.root / "oracle"
        sealed = write_oracle(target, result, documents=self.documents, queries=self.queries, permille=1000,
                              rule=COHORT_RULE, eligible=eligible, k=10, implementation=A,
                              observation={"device": "cpu"})
        body, rows, scores = read_oracle(target)
        self.assertEqual(body, sealed["body"])
        np.testing.assert_array_equal(rows, result.rows)
        changed = scores.copy()
        changed[0, 0] += 1e-9
        np.save(target / "scores.npy", changed)
        with self.assertRaisesRegex(OracleError, "does not match"):
            read_oracle(target)

    def test_quantized_sets_are_not_canonical_rows(self):
        key = SpaceKey(encoder="synthetic/test", revision="r1", files={"weights": A}, query_template="{text}",
                       document_template="{text}", pooling="none", normalize=True, max_tokens=8, dtype="int8",
                       dimensions=16, mrl_dimensions=16, quantization="int8-round127")
        writer = VectorSetWriter(self.root / "int8", Identity(key, "document", {"corpus": A, "transform": B}),
                                 shard_rows=4, derived_from={"vector_set": A, "operation": {"kind": "int8"}})
        writer.append(["a"], np.ones((1, 16), dtype=np.int8), 0)
        writer.finish(1)
        with self.assertRaisesRegex(VectorStoreError, "quantized"):
            CanonicalRows(self.root / "int8")
