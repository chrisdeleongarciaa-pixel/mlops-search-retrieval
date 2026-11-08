import unittest
from prepare_litsearch import development_queries, recall_scores, select_queries


class LitSearchContract(unittest.TestCase):
    def setUp(self):
        self.queries = [{"id": str(i), "query_set": "author" if i < 6 else "inline", "specificity": i % 2,
                         "query": f"question {i}", "corpusids": [str(i)]} for i in range(12)]

    def test_selection_is_stable_under_input_permutation(self):
        chosen = select_queries(self.queries, 6)
        self.assertEqual(chosen, select_queries(list(reversed(self.queries)), 6))
        self.assertEqual(len(chosen), 6)

    def test_related_chain_excluded_from_development(self):
        self.queries[1]["corpusids"] = ["0", "1"]
        self.queries[2]["corpusids"] = ["1", "2"]
        remaining = development_queries(self.queries, [self.queries[0]])
        self.assertEqual({q["id"] for q in remaining}, {str(i) for i in range(3, 12)})

    def test_recall_oracle_and_missing_queries(self):
        query = {**self.queries[0], "corpusids": ["a", "b"]}
        docs = ["x", "a", "y", "z", "w", "b"]
        self.assertEqual(recall_scores([query], {"0": docs})["0"], {"Recall@5": .5, "Recall@20": 1.0})
        with self.assertRaises(ValueError):
            recall_scores([query], {})
        with self.assertRaises(ValueError):
            recall_scores([query], {"0": ["a", "a"]})
