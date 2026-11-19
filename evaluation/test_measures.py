"""Expected values are calculated by hand from the metric definitions, not taken from the evaluator."""

from math import log2
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bench import answer_every_query, directory_digest
from measures import MEASURES, aggregate, evaluate


QRELS = {"q1": {"d1": 1, "d2": 0, "d3": 2}, "q2": {"d9": 1}, "q3": {"z": 1}}


class MeasureTests(unittest.TestCase):
    def test_graded_ranking_matches_hand_calculation(self):
        # Ranking d2 (rel 0), dX (unjudged), d3 (rel 2), d1 (rel 1).
        scores = evaluate({"q1": [("d2", 9.0), ("dX", 8.0), ("d3", 7.0), ("d1", 6.0)]}, QRELS)["q1"]
        dcg = 2 / log2(3 + 1) + 1 / log2(4 + 1)
        ideal = 2 / log2(1 + 1) + 1 / log2(2 + 1)
        self.assertAlmostEqual(scores["nDCG@10"], dcg / ideal, places=12)
        self.assertAlmostEqual(scores["nDCG@10"], 0.5437912419, places=9)
        self.assertEqual(scores["RR@10"], 1 / 3)
        self.assertEqual(scores["R@10"], 1.0)
        self.assertEqual(scores["Judged@10"], 3 / 4)
        self.assertEqual(set(scores), set(MEASURES))

    def test_cutoffs_apply_to_rank_not_to_list_length(self):
        hits = [(f"n{rank}", 100.0 - rank) for rank in range(1, 11)] + [("d9", 1.0)]
        scores = evaluate({"q2": hits}, QRELS)["q2"]
        self.assertEqual((scores["RR@10"], scores["R@10"], scores["nDCG@10"], scores["Judged@10"]), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual((scores["R@100"], scores["R@1000"]), (1.0, 1.0))

    def test_list_order_is_the_ranking_even_with_tied_scores(self):
        # trec_eval breaks score ties by descending document ID, which would put "z" first.
        scores = evaluate({"q3": [("a", 1.0), ("z", 1.0)]}, QRELS)["q3"]
        self.assertEqual(scores["RR@10"], 0.5)
        self.assertAlmostEqual(scores["nDCG@10"], 1 / log2(3), places=12)

    def test_empty_ranking_scores_zero_and_enters_the_mean(self):
        scores = evaluate({"q2": [("d9", 1.0)], "q3": []}, QRELS)
        self.assertEqual(scores["q3"], dict.fromkeys(MEASURES, 0.0))
        self.assertEqual(aggregate(scores)["RR@10"], 0.5)
        self.assertEqual(aggregate(scores)["Judged@10"], 0.5)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "without judgments"):
            evaluate({"unknown": [("d1", 1.0)]}, QRELS)
        with self.assertRaisesRegex(ValueError, "repeats a document"):
            evaluate({"q1": [("d1", 2.0), ("d1", 1.0)]}, QRELS)
        with self.assertRaisesRegex(ValueError, "canonical form"):
            evaluate({"q1": [("d1", 1.0)]}, QRELS, ("ndcg_cut_10",))
        with self.assertRaises(ValueError):
            aggregate({})


class DirectoryDigestTests(unittest.TestCase):
    def test_digest_follows_names_sizes_and_content(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "a/segments").write_bytes(b"one")
            (root / "b.bin").write_bytes(b"two")
            # printf 'a/segments\t3\t%s\nb.bin\t3\t%s\n' "$(printf one | sha256sum | cut -c1-64)" \
            #   "$(printf two | sha256sum | cut -c1-64)" | sha256sum
            first = directory_digest(root)
            self.assertEqual(first, "927d2435d47af78cf53ac74594c55782df810b4158919f1120ddcbb4bc4bb679")
            (root / "b.bin").write_bytes(b"tw0")
            changed = directory_digest(root)
            (root / "b.bin").write_bytes(b"two")
            (root / "b.bin").rename(root / "c.bin")
            self.assertEqual(len({first, changed, directory_digest(root)}), 3)


class EngineOutputTests(unittest.TestCase):
    QUERIES = {"q1": "a", "q2": "b", "q3": "c"}

    def test_omitted_queries_are_answered_with_no_hits(self):
        produced = [("q1", [("d1", 1.0)]), ("q3", [("d2", 2.0)])]
        self.assertEqual(list(answer_every_query(self.QUERIES, produced)),
                         [("q1", [("d1", 1.0)]), ("q2", []), ("q3", [("d2", 2.0)])])
        self.assertEqual(list(answer_every_query(self.QUERIES, [])), [("q1", []), ("q2", []), ("q3", [])])

    def test_unknown_or_unordered_engine_queries_are_rejected(self):
        for produced in ([("q9", [])], [("q3", []), ("q1", [])], [("q1", []), ("q1", [])]):
            with self.subTest(produced=produced), self.assertRaisesRegex(ValueError, "unknown or out-of-order"):
                list(answer_every_query(self.QUERIES, produced))
