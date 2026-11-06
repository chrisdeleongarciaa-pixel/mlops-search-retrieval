import json
from pathlib import Path
import unittest

from search_retrieval.query.ranking import Ranked, exact_cosine, fuse


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
