import unittest
from types import SimpleNamespace

from search_retrieval.adapters.vespa import VespaWitness
from search_retrieval.query.eligible import Eligibility, Hit, SearchUnavailable
from search_retrieval.query.vector import VectorSearch


class Authority:
    def __init__(self, *snapshots):
        self.snapshots = iter(snapshots)

    def eligibility(self, tenant):
        return next(self.snapshots)


class Encoder:
    space = SimpleNamespace(id="fixture")

    def encode(self, texts, *, role):
        return [[1.0] + [0.0] * 767]


class FixtureIndex(VespaWitness):
    def __init__(self, vector=True, full=True):
        self.vector = vector
        self.full = full

    def _post(self, path, body, timeout):
        return {"root": {"coverage": {"full": self.full}, "fields": {"totalCount": 1}, "children": [{
            "fields": {"projection_id": "p", "fragment_id": "f", "vector_space": "fixture",
                       "embedding": {"values": [1.0] + [0.0] * 767} if self.vector else None},
        }]}}

    def dense(self, eligibility, vector, space, timeout, **options):
        return (Hit("p", "f", "evidence", 1.0),)

    def lexical(self, eligibility, query, k, timeout):
        return (Hit("p", "f", "evidence", 2.0),)


class VectorContract(unittest.TestCase):
    snapshot = Eligibility("lab", "g", frozenset({"p"}))
    empty = Eligibility("lab", "g", frozenset())

    def test_empty_is_valid_and_incompatible_generation_is_explicit(self):
        search = VectorSearch(Authority(self.empty, self.empty), None, None, "g")
        self.assertEqual(search.search("lab", "query").hits, ())
        search = VectorSearch(Authority(self.empty), None, None, "other")
        with self.assertRaisesRegex(SearchUnavailable, "generation"):
            search.search("lab", "query")

    def test_missing_vector_and_partial_coverage_fail_closed(self):
        for mode in ("dense", "hybrid"):
            for index, error in ((FixtureIndex(vector=False), "Missing"), (FixtureIndex(full=False), "incomplete")):
                with self.subTest(mode=mode, error=error), self.assertRaisesRegex(SearchUnavailable, error):
                    VectorSearch(Authority(self.snapshot), index, Encoder(), "g").search("lab", "query", mode=mode)

    def test_retraction_during_hybrid_ranking_retries_on_the_empty_snapshot(self):
        authority = Authority(self.snapshot, self.empty, self.empty, self.empty)
        attempts = []
        result = VectorSearch(authority, FixtureIndex(), Encoder(), "g").search(
            "lab", "query", mode="hybrid", attempts=attempts,
        )
        self.assertEqual(result.hits, ())
        self.assertEqual([(row["attempt"], row["snapshot_stable"]) for row in attempts], [(1, False), (2, True)])
        self.assertTrue({"eligibility_ms", "encode_query_ms", "vespa_dense_ms",
                         "vespa_lexical_ms", "revalidation_ms"} <= attempts[0].keys())
        self.assertNotIn("encode_query_ms", attempts[1])


if __name__ == "__main__":
    unittest.main()
