from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from paired_bootstrap import paired_interval, read_scores


class PairedBootstrapTests(unittest.TestCase):
    def test_constant_paired_gain_has_degenerate_interval(self):
        left = {"q2": 0.75, "q1": 0.5, "q3": 1.0}
        right = {"q1": 0.25, "q3": 0.75, "q2": 0.5}
        result = paired_interval(left, right, seed=7, resamples=100)
        self.assertEqual(result["left_minus_right"], {"mean": 0.25, "ci95": [0.25, 0.25]})
        self.assertEqual(result["wins_ties_losses"], [3, 0, 0])

    def test_build_average_retains_each_query_once(self):
        with TemporaryDirectory() as directory:
            a = Path(directory) / "a.json"
            b = Path(directory) / "b.json"
            a.write_text('{"ann":{"q2":{"ndcg":1},"q1":{"ndcg":0}}}', encoding="utf-8")
            b.write_text('{"ann":{"q1":{"ndcg":1},"q2":{"ndcg":1}}}', encoding="utf-8")
            self.assertEqual(read_scores([a, b], "ann", "ndcg"), {"q1": 0.5, "q2": 1.0})
            b.write_text('{"ann":{"q2":{"ndcg":1}}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identical query IDs"):
                read_scores([a, b], "ann", "ndcg")

    def test_reads_the_per_query_records_of_a_hashed_run(self):
        with TemporaryDirectory() as directory:
            run = Path(directory) / "per-query.jsonl"
            run.write_text('{"metrics":{"nDCG@10":0.25},"qid":"q1"}\n{"metrics":{"nDCG@10":1.0},"qid":"q2"}\n',
                           encoding="utf-8")
            self.assertEqual(read_scores([run], "run", "nDCG@10"), {"q1": 0.25, "q2": 1.0})
            with self.assertRaisesRegex(ValueError, 'ranker name "run"'):
                read_scores([run], "exact", "nDCG@10")
            run.write_text('{"metrics":{"nDCG@10":0.25},"qid":"q1"}\n' * 2, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate query IDs"):
                read_scores([run], "run", "nDCG@10")

    def test_rejects_unmatched_pairs(self):
        with self.assertRaisesRegex(ValueError, "identical"):
            paired_interval({"q1": 0.5}, {"q2": 0.5})
