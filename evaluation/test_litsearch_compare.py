"""The LitSearch comparison uses the paper's measures: R@20 on broad, R@5 on specific questions."""

import unittest

import numpy as np

from litsearch_compare import PUBLISHED, interval, recall, score


class LitSearchCompareTests(unittest.TestCase):
    def test_recall_and_the_published_averages(self):
        self.assertEqual(recall(["a", "b", "c"], {"b", "z"}, 2), 0.5)
        # The paper's averages are question-weighted: BM25 broad R@20 = (120 * 37.4 + 35 * 48.6) / 155.
        self.assertAlmostEqual((120 * 37.4 + 35 * 48.6) / 155, PUBLISHED["BM25"]["broad_R@20"], places=1)

    def test_score_splits_by_specificity_and_counts_missing_runs_as_zero(self):
        questions = [{"id": "q1", "specificity": 1, "corpusids": ["a"]},
                     {"id": "q2", "specificity": 0, "corpusids": ["b", "c"]},
                     {"id": "q3", "specificity": 1, "corpusids": ["d"]}]
        run = {"q1": [("x", 3.0), ("a", 2.0)], "q2": [("c", 1.0)]}
        result = score(run, questions)
        self.assertEqual(result["specific_R@5"]["mean"], 50.0)
        self.assertEqual(result["specific_R@5"]["questions"], 2)
        self.assertEqual(result["broad_R@20"]["mean"], 50.0)
        with self.assertRaises(ValueError):
            score(run, [{"id": "q9", "specificity": 2, "corpusids": ["a"]}])

    def test_interval_brackets_the_mean(self):
        values = np.array([0.0, 100.0] * 50)
        low, high = interval(values)
        self.assertLess(low, 50.0)
        self.assertGreater(high, 50.0)


if __name__ == "__main__":
    unittest.main()
