"""Vespa passage client against an HTTP fake, and the exact-parity rule of step 5."""

import unittest

import numpy as np

from search_retrieval.adapters.vespa_passages import Passage, VespaPassages, document_id, wait_until
from search_retrieval.contracts.cohorts import cohort_bucket
from search_retrieval.query.eligible import SearchUnavailable
from engine_parity import exact_agreement
from fake_services import FakeVespa


def unit(rows):
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.vespa = FakeVespa(overload=3)
        self.addCleanup(self.vespa.close)
        self.client = VespaPassages(self.vespa.url, timeout=10)
        rng = np.random.default_rng(4)
        self.vectors = unit(rng.standard_normal((40, 8)))
        self.ids = [f"doc-{index:02d}" for index in range(40)]
        self.passages = [Passage(item, f"word{index % 5} common", self.vectors[index].tolist(), cohort_bucket(item),
                                 ("public",) if index % 4 else ("group-a",)) for index, item in enumerate(self.ids)]

    def test_feed_retries_overload_and_reads_back_exact_float32_rows(self):
        self.assertEqual(self.client.feed("c1", iter(self.passages), threads=4), 40)
        self.assertEqual(self.vespa.overload, 0)
        self.assertEqual(self.client.count("c1"), 40)
        self.assertEqual(self.client.count("other"), 0)
        fields = self.client.get("c1", "doc-07")
        np.testing.assert_array_equal(np.asarray(fields["embedding"], dtype=np.float32), self.vectors[7])
        self.assertEqual(document_id("c1", "doc-07"), "c1.doc-07")

    def test_dense_and_lexical_queries_apply_collection_cohort_and_acl_filters(self):
        self.client.feed("c1", iter(self.passages), threads=4)
        query = self.vectors[3].tolist()
        hits, _ = self.client.dense("c1", query, 5, approximate=False)
        expected = np.argsort(-(self.vectors.astype(np.float64) @ self.vectors[3].astype(np.float64)), kind="stable")[:5]
        self.assertEqual([hit.doc_id for hit in hits], [self.ids[index] for index in expected])
        below = 300
        hits, _ = self.client.dense("c1", query, 40, cohort_below=below)
        self.assertTrue(hits and all(cohort_bucket(hit.doc_id) < below for hit in hits))
        restricted, _ = self.client.dense("c1", query, 40, tokens=["group-a"])
        self.assertEqual({hit.doc_id for hit in restricted}, {item for index, item in enumerate(self.ids) if index % 4 == 0})
        lexical = self.client.lexical("c1", "word2", 40)
        self.assertEqual({hit.doc_id for hit in lexical}, {item for index, item in enumerate(self.ids) if index % 5 == 2})
        sent = self.vespa.queries[-1]
        self.assertIn('{grammar: "any", defaultIndex: "text"}userInput(@q)', sent["yql"])
        self.assertEqual(sent["q"], "word2")

    def test_revoking_a_token_removes_the_document_from_filtered_results(self):
        self.client.feed("c1", iter(self.passages), threads=4)
        self.client.update_tokens("c1", "doc-00", [])
        elapsed = wait_until(lambda: "doc-00" not in {hit.doc_id for hit in self.client.dense(
            "c1", self.vectors[0].tolist(), 40, tokens=["group-a"])[0]}, seconds=2)
        self.assertIsNotNone(elapsed)

    def test_invalid_requests_and_responses_fail(self):
        with self.assertRaises(ValueError):
            VespaPassages("https://example.invalid")
        with self.assertRaises(ValueError):
            self.client.dense("c1", [0.0] * 8, 5, tokens=[])
        with self.assertRaises(ValueError):
            self.client.dense("c1", [0.0] * 8, 5, cohort_below=1001)
        with self.assertRaises(SearchUnavailable):
            self.client.get("c1", "missing")
        original = self.vespa.search
        self.vespa.search = lambda body: {"root": {"coverage": {"full": False}}}
        try:
            with self.assertRaisesRegex(SearchUnavailable, "coverage"):
                self.client.count("c1")
        finally:
            self.vespa.search = original


class ExactAgreementTests(unittest.TestCase):
    ORACLE_ROWS = np.array([4, 9, 2, 7])
    ORACLE_SCORES = np.array([0.9, 0.8, 0.70000, 0.69999])

    def test_identical_lists_agree(self):
        result = exact_agreement([4, 9, 2, 7], [0.9, 0.8, 0.7, 0.69999], self.ORACLE_ROWS, self.ORACLE_SCORES,
                                 np.array([0.9, 0.8, 0.7, 0.69999]))
        self.assertTrue(result["agrees"])

    def test_a_tie_at_the_boundary_may_swap_members(self):
        # Document 5 ties the oracle's last score within 1e-5 and replaces 7.
        result = exact_agreement([4, 9, 2, 5], [0.9, 0.8, 0.7, 0.69999], self.ORACLE_ROWS, self.ORACLE_SCORES,
                                 np.array([0.9, 0.8, 0.7, 0.699995]))
        self.assertTrue(result["agrees"])
        self.assertEqual(result["differing"], 1)

    def test_a_missing_neighbour_or_a_score_gap_disagrees(self):
        replaced = exact_agreement([4, 9, 2, 5], [0.9, 0.8, 0.7, 0.69999], self.ORACLE_ROWS, self.ORACLE_SCORES,
                                   np.array([0.9, 0.8, 0.7, 0.5]))
        self.assertFalse(replaced["agrees"])
        shifted = exact_agreement([4, 9, 2, 7], [0.9, 0.8, 0.7, 0.6999], self.ORACLE_ROWS, self.ORACLE_SCORES,
                                  np.array([0.9, 0.8, 0.7, 0.69999]))
        self.assertFalse(shifted["agrees"])
        short = exact_agreement([4, 9, 2], [0.9, 0.8, 0.7], self.ORACLE_ROWS, self.ORACLE_SCORES, np.array([0.9, 0.8, 0.7]))
        self.assertFalse(short["agrees"])


class BenchSystemTests(unittest.TestCase):
    """The Vespa systems of bench.py, with the projection and query vectors replaced by fakes."""

    def setUp(self):
        import bench
        from search_retrieval.query.ranking import Ranked, convex, fuse

        self.bench, self.Ranked, self.convex, self.fuse = bench, Ranked, convex, fuse
        self.vespa = FakeVespa()
        self.addCleanup(self.vespa.close)
        self.client = VespaPassages(self.vespa.url, timeout=10)
        rng = np.random.default_rng(8)
        self.vectors = unit(rng.standard_normal((30, 8)))
        self.client.feed("c1", iter(Passage(f"d{index:02d}", f"t{index % 3} shared", self.vectors[index].tolist(),
                                            cohort_bucket(f"d{index:02d}")) for index in range(30)), threads=2)
        self.queries = {"q1": "t1", "q2": "t2 shared"}
        self.query_vectors = {"q1": self.vectors[1].tolist(), "q2": self.vectors[5].tolist()}
        patches = [("vespa_projection", lambda resolved: ({}, self.client)),
                   ("query_vectors", lambda resolved, queries: (self.query_vectors, "a" * 64))]
        for name, value in patches:
            original = getattr(bench, name)
            setattr(bench, name, value)
            self.addCleanup(setattr, bench, name, original)

    def resolved(self, profile, fusion, lexical=None):
        from types import SimpleNamespace

        index = {"engine": "vespa", "schema_revision": "c1"}
        if lexical is not None:
            index["lexical"] = lexical
        return SimpleNamespace(config={"index": index, "ranking": {"profile": profile, "fusion": fusion, "target_hits": 10}})

    def run_system(self, *arguments):
        stream, strategy, extra = self.bench.vespa_system(self.resolved(*arguments), None, self.queries, None)
        return dict(stream), strategy, extra

    def test_branches_and_fusions_follow_their_definitions(self):
        lexical, _, _ = self.run_system("vespa-bm25", {"kind": "none"}, {"profile": "bm25"})
        dense, strategy, extra = self.run_system("vespa-dense", {"kind": "none"})
        self.assertEqual(strategy, "engine-reported")
        self.assertEqual(extra, {"query_vectors": "a" * 64})
        rrf, _, _ = self.run_system("vespa-hybrid", {"kind": "rrf", "k": 60}, {"profile": "bm25"})
        mixed, _, _ = self.run_system("vespa-hybrid", {"kind": "convex", "alpha": 0.3, "normalization": "minmax"},
                                      {"profile": "bm25"})
        for qid in self.queries:
            branches = ([self.Ranked(doc, score) for doc, score in lexical[qid]],
                        [self.Ranked(doc, score) for doc, score in dense[qid]])
            self.assertEqual([doc for doc, _ in rrf[qid]], [hit.id for hit in self.fuse(*branches, depth=10).fused][:10])
            self.assertEqual([doc for doc, _ in mixed[qid]],
                             [hit.id for hit in self.convex(*branches, alpha=0.3, depth=10).fused][:10])
            self.assertLessEqual(len(rrf[qid]), 10)

    def test_inconsistent_configurations_are_rejected(self):
        from search_retrieval.experiment.run_config import ConfigError

        cases = [("vespa-dense", {"kind": "rrf"}, None), ("vespa-hybrid", {"kind": "none"}, {"profile": "bm25"}),
                 ("vespa-bm25", {"kind": "none"}, None), ("vespa-hybrid", {"kind": "rrf", "k": 10}, {"profile": "bm25"}),
                 ("vespa-hybrid", {"kind": "convex", "alpha": 0.5}, {"profile": "bm25"}),
                 ("vespa-hybrid", {"kind": "weighted"}, {"profile": "bm25"})]
        for profile, fusion, lexical in cases:
            with self.subTest(profile=profile, fusion=fusion), self.assertRaises(ConfigError):
                self.run_system(profile, fusion, lexical)
