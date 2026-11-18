import json
from pathlib import Path
import unittest

from search_retrieval.query.ranking import Ranked, convex, exact_cosine, fuse, min_max


class RankingTests(unittest.TestCase):
    def test_exact_witness_filters_tenant_before_cutoff(self):
        fixture = Path(__file__).resolve().parents[2] / "evaluation/fixtures/witness.json"
        data = json.loads(fixture.read_bytes())
        docs = data["documents"]
        hits = exact_cosine(
            [1.0, 0.0], {key: doc["vector"] for key, doc in docs.items()},
            frozenset(key for key, doc in docs.items() if doc["tenant"] == "lab"), k=2,
        )
        self.assertEqual([hit.id for hit in hits], ["es-protocolo", "es-distractor"])

    def test_rrf_uses_independent_lists_and_id_tie_break(self):
        result = fuse([Ranked("c", 4), Ranked("b", 3)], [Ranked("a", .9), Ranked("b", .1)])
        self.assertEqual([hit.id for hit in result.fused], ["b", "a", "c"])
        self.assertAlmostEqual(result.fused[0].score, 2 / 62)
        self.assertEqual([hit.id for hit in result.lexical], ["c", "b"])

    def test_duplicate_invalidates_branch(self):
        with self.assertRaises(ValueError):
            fuse([Ranked("a", 1), Ranked("a", 0)], [])


class ConvexFusionTests(unittest.TestCase):
    def test_min_max_scaling_by_hand(self):
        self.assertEqual(min_max([Ranked("a", 4.0), Ranked("b", 2.0), Ranked("c", 3.0)]), {"a": 1.0, "b": 0.0, "c": 0.5})
        self.assertEqual(min_max([Ranked("a", 0.3), Ranked("b", 0.3)]), {"a": 1.0, "b": 1.0})
        self.assertEqual(min_max([]), {})

    def test_convex_combination_by_hand(self):
        lexical = [Ranked("a", 10.0), Ranked("b", 6.0), Ranked("c", 2.0)]   # scaled 1, 0.5, 0
        dense = [Ranked("b", 0.9), Ranked("d", 0.5)]                        # scaled 1, 0
        fused = convex(lexical, dense, alpha=0.4)
        # a: 0.6 * 1; b: 0.4 * 1 + 0.6 * 0.5 = 0.7; c: 0; d: 0.4 * 0 = 0.
        self.assertEqual([hit.id for hit in fused.fused], ["b", "a", "c", "d"])
        self.assertAlmostEqual(fused.fused[0].score, 0.7)
        self.assertAlmostEqual(fused.fused[1].score, 0.6)

    def test_alpha_ends_reproduce_each_branch_order(self):
        lexical = [Ranked("a", 3.0), Ranked("b", 2.0), Ranked("c", 1.0)]
        dense = [Ranked("c", 0.9), Ranked("b", 0.8), Ranked("a", 0.1)]
        self.assertEqual([hit.id for hit in convex(lexical, dense, alpha=0.0).fused], ["a", "b", "c"])
        self.assertEqual([hit.id for hit in convex(lexical, dense, alpha=1.0).fused], ["c", "b", "a"])

    def test_invalid_alpha_depth_and_branches_are_rejected(self):
        for alpha in (-0.1, 1.1):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                convex([], [], alpha=alpha)
        with self.assertRaises(ValueError):
            convex([Ranked("a", 1.0), Ranked("a", 0.5)], [], alpha=0.5)
        with self.assertRaises(ValueError):
            convex([Ranked("a", float("nan"))], [], alpha=0.5)
