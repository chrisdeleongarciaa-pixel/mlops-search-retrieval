"""Offline fusion: candidate runs, the choice rule and the promotion gate."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import fusion_sweep
from fusion_sweep import ALPHAS, branch, choose, fused, gate


class FusionSweepTests(unittest.TestCase):
    def test_alpha_grid_and_fused_runs(self):
        self.assertEqual((ALPHAS[0], ALPHAS[-1], len(ALPHAS)), (0.0, 1.0, 21))
        lexical = {"q": [("a", 3.0), ("b", 1.0)]}
        dense = {"q": [("b", 0.9), ("c", 0.1)]}
        self.assertEqual([doc for doc, _ in fused(lexical, dense, {"kind": "convex", "alpha": 0.0})["q"]][:1], ["a"])
        self.assertEqual([doc for doc, _ in fused(lexical, dense, {"kind": "convex", "alpha": 1.0})["q"]][:1], ["b"])
        self.assertEqual([doc for doc, _ in fused(lexical, dense, {"kind": "rrf", "k": 60})["q"]][:1], ["b"])

    def test_branch_keeps_queries_without_results(self):
        # run.trec has no line for a query without hits; per-query.jsonl has one record per query.
        # Observed on LitSearch dev: BM25 answered fewer queries than dense and select() failed.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run.trec").write_text("q1 Q0 a 1 2.5 tag\nq3 Q0 b 1 1.0 tag\n", encoding="utf-8")
            (root / "per-query.jsonl").write_text(
                "".join(json.dumps({"qid": qid, "metrics": {}}) + "\n" for qid in ("q1", "q2", "q3")), encoding="utf-8")
            with mock.patch.object(fusion_sweep, "verify_outputs", return_value={"queries": 3}):
                _, run = branch(root)
            self.assertEqual({qid: [(doc, float(score)) for doc, score in hits] for qid, hits in run.items()},
                             {"q1": [("a", 2.5)], "q2": [], "q3": [("b", 1.0)]})
            with mock.patch.object(fusion_sweep, "verify_outputs", return_value={"queries": 4}), \
                    self.assertRaises(ValueError):
                branch(root)

    def test_choice_prefers_the_mean_then_rrf_then_the_smaller_alpha(self):
        rows = [{"label": "convex-0.30", "method": {"kind": "convex", "alpha": 0.3}, "nDCG@10": 0.5},
                {"label": "convex-0.10", "method": {"kind": "convex", "alpha": 0.1}, "nDCG@10": 0.5},
                {"label": "rrf", "method": {"kind": "rrf"}, "nDCG@10": 0.49}]
        self.assertEqual(choose(rows)["label"], "convex-0.10")
        rows[2]["nDCG@10"] = 0.5
        self.assertEqual(choose(rows)["label"], "rrf")

    def test_gate_promotes_only_a_gain_whose_interval_excludes_zero(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)

            def write(name, scores):
                (root / name).mkdir()
                path = root / name / "per-query.jsonl"
                path.write_text("".join(json.dumps({"qid": f"q{index}", "metrics": {"nDCG@10": value}}) + "\n"
                                        for index, value in enumerate(scores)), encoding="utf-8")
                return path

            branch = write("dense", [0.5] * 40)
            better = write("fused-better", [0.6] * 40)
            noisy = write("fused-noisy", [0.9, 0.1] * 20)
            self.assertTrue(gate(better, [branch])["promoted"])
            self.assertFalse(gate(noisy, [branch])["promoted"])
            self.assertEqual(gate(better, [branch, write("lexical", [0.2] * 40)])["better_branch"], "dense")
