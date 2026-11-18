"""Offline fusion: candidate runs, the choice rule and the promotion gate."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fusion_sweep import ALPHAS, choose, fused, gate


class FusionSweepTests(unittest.TestCase):
    def test_alpha_grid_and_fused_runs(self):
        self.assertEqual((ALPHAS[0], ALPHAS[-1], len(ALPHAS)), (0.0, 1.0, 21))
        lexical = {"q": [("a", 3.0), ("b", 1.0)]}
        dense = {"q": [("b", 0.9), ("c", 0.1)]}
        self.assertEqual([doc for doc, _ in fused(lexical, dense, {"kind": "convex", "alpha": 0.0})["q"]][:1], ["a"])
        self.assertEqual([doc for doc, _ in fused(lexical, dense, {"kind": "convex", "alpha": 1.0})["q"]][:1], ["b"])
        self.assertEqual([doc for doc, _ in fused(lexical, dense, {"kind": "rrf", "k": 60})["q"]][:1], ["b"])

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
