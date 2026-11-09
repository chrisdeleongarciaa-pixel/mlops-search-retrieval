import unittest

from search_retrieval.query.ranking import exact_cosine
from scifact_dense import cosine_matrix, parity, top_exact, trace_plan, traced_candidates


class DenseEvaluationTests(unittest.TestCase):
    def test_manual_cosines_match_the_independent_scalar_oracle(self):
        vectors = {"best": [1, 0], "scaled": [2, 0], "other": [0, 1], "excluded": [1, 0]}
        ids = sorted(vectors.keys() - {"excluded"})
        scores, = cosine_matrix(ids, [vectors[doc] for doc in ids], [[3, 0]])
        run = top_exact(dict(zip(ids, map(float, scores), strict=True)))
        scalar = exact_cosine([3, 0], vectors, frozenset(ids), k=100)
        self.assertEqual(list(run.items()), [(hit.id, hit.score) for hit in scalar])

    def test_cutoff_exchange_preserves_strict_overlap(self):
        scores = {f"top-{n}": 1 - n / 100 for n in range(9)}
        scores.update({"a": .9, "b": .899999, "c": .8})
        expected = dict(list(top_exact(scores).items())[:10])
        actual = dict(list(expected.items())[:-1] + [("b", .899999)])
        cut = parity(expected, actual, 10, scores)["10"]
        self.assertEqual((cut["strict_overlap"], cut["tie_exchanges"], cut["outside_tolerance"]), (.9, 1, 0))
        self.assertTrue(cut["passed"])
        actual = dict(list(expected.items())[:-1] + [("c", .8)])
        self.assertFalse(parity(expected, actual, 10, scores)["10"]["passed"])

    def test_pinned_trace_algorithm_and_depth(self):
        trace = {"children": [{"message": {"algorithm": "index top k using filter", "top_k_hits": 112}}]}
        self.assertEqual(trace_plan(trace, True), "hnsw")
        self.assertEqual(traced_candidates(trace), 112)
        self.assertEqual(trace_plan({"algorithm": "exact fallback"}, True), "exact_by_selectivity")
        self.assertEqual(trace_plan({"algorithm": "exact"}, False), "exact_requested")
        self.assertEqual(trace_plan({"algorithm": "exact"}, True), "indeterminate")
        self.assertEqual(trace_plan(None, True), "indeterminate")
        estimate = {"children": [
            {"decision": "Skip", "parameters": {"estimated_hit_ratio": .0096, "lower_limit": .02, "upper_limit": 1.0}},
            {"optimized": {"algorithm": "exact", "wanted_approximate": True, "has_index": True}},
        ]}
        self.assertEqual(trace_plan(estimate, True), "exact_by_selectivity")
        estimate["children"][0]["parameters"]["estimated_hit_ratio"] = .05
        self.assertEqual(trace_plan(estimate, True), "indeterminate")


if __name__ == "__main__":
    unittest.main()
