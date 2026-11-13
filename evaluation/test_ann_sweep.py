"""ANN sweeps: recall definitions, frontier, configuration and FAISS builds against the oracle.

Recall and frontier expectations are calculated by hand from their definitions.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import numpy as np

from search_retrieval.contracts.cohorts import cohort_buckets, cohort_mask
from ann_sweep import (
    SweepError, completed, expand, frontier, insertion_order, parse_sweep, recall, run_build,
    summarize,
)
from oracle import FlatSearcher, exact_top_k
from test_oracle import close_opened, unit, write


CONFIG = """
[inputs]
space = "manifests/space/x.json"
corpus = "manifests/corpus/x.json"
transform = "manifests/transform/x.json"
queries = "manifests/queries/x.json"

[oracle]
k = 20
initial_candidates = 40
query_batch = 64
cohorts_permille = [1000, 100, 10]

[measure]
recall_at = [5, 20]
search_threads = 1
build_threads = 1
timing_repeats = 2
warmup = 0
add_batch = 100

[[hnsw]]
name = "grid"
m = [4, 8]
ef_construction = [20]
insertion_seeds = [11, 29]
ef_search = [20, 400]
cohorts_permille = [1000, 100, 10]

[[ivfpq]]
name = "lowmem"
nlist = [8]
pq_m = [4]
nbits = 4
train_size = 300
train_seed = 11
insertion_seeds = [11]
nprobe = [1, 8]
cohorts_permille = [1000]
"""


class RecallTests(unittest.TestCase):
    def test_strict_and_tie_aware_recall_by_hand(self):
        exact = np.array([[3, 1, 2, -1], [7, 8, 9, 10], [-1, -1, -1, -1]])
        exact_values = np.array([[0.9, 0.8, 0.8, -np.inf], [0.5, 0.4, 0.3, 0.2], [-np.inf] * 4])
        # Query 0 has three eligible documents. The index returns 3, 5 (tied with the boundary 0.8) and nothing.
        # Query 1 returns 8, 7, 11 (11 scores 0.1, below the boundary) and 9.
        found = np.array([[3, 5, -1, -1], [8, 7, 11, 9], [4, 4, 4, 4]])
        found_values = np.array([[0.9, 0.8, -np.inf, -np.inf], [0.4, 0.5, 0.1, 0.3], [0.0] * 4])
        strict, tie = recall(found, found_values, exact, exact_values, 4)
        np.testing.assert_allclose(strict[:2], [1 / 3, 3 / 4])
        np.testing.assert_allclose(tie[:2], [2 / 3, 3 / 4])
        self.assertTrue(np.isnan(strict[2]) and np.isnan(tie[2]))
        strict, tie = recall(found, found_values, exact, exact_values, 2)
        np.testing.assert_allclose(strict[:2], [1 / 2, 2 / 2])
        np.testing.assert_allclose(tie[:2], [2 / 2, 2 / 2])

    def test_summary_quantiles_thresholds_and_exclusions(self):
        values = np.array([1.0, 0.98, 0.9, np.nan, 1.0])
        summary = summarize(values)
        self.assertEqual((summary["queries"], summary["excluded"]), (4, 1))
        self.assertAlmostEqual(summary["mean"], 0.97)
        self.assertEqual((summary["q00"], summary["below_0.95"], summary["below_0.99"]), (0.9, 0.25, 0.5))
        self.assertEqual(summarize(np.array([np.nan])), {"queries": 0, "excluded": 1})

    def test_frontier_keeps_only_undominated_points(self):
        points = [{"recall": 0.9, "qps": 100}, {"recall": 0.95, "qps": 80}, {"recall": 0.9, "qps": 90},
                  {"recall": 0.99, "qps": 10}, {"recall": 0.95, "qps": 80}, {"recall": 0.8, "qps": 50}]
        self.assertEqual([(point["recall"], point["qps"]) for point in frontier(points)],
                         [(0.9, 100), (0.95, 80), (0.95, 80), (0.99, 10)])

    def test_insertion_order_is_a_permutation_fixed_by_the_seed(self):
        order = insertion_order(1000, 11)
        self.assertEqual(sorted(order.tolist()), list(range(1000)))
        np.testing.assert_array_equal(order, insertion_order(1000, 11))
        self.assertFalse(np.array_equal(order, insertion_order(1000, 29)))


class ConfigTests(unittest.TestCase):
    def test_builds_expand_the_grid_with_distinct_labels(self):
        builds = expand(parse_sweep(CONFIG.encode()))
        self.assertEqual(len(builds), 2 * 2 + 1)
        self.assertEqual(len({build.label for build in builds}), 5)
        self.assertIn("hnsw-grid-efc20-m4-s11", {build.label for build in builds})

    def test_invalid_configurations_are_rejected(self):
        cases = {
            "unknown section": CONFIG + "\n[extra]\nx = 1\n",
            "missing field": CONFIG.replace("warmup = 0\n", ""),
            "oracle too shallow": CONFIG.replace("k = 20", "k = 10"),
            "ef below depth": CONFIG.replace("ef_search = [20, 400]", "ef_search = [10, 400]"),
            "cohort without oracle": CONFIG.replace("cohorts_permille = [1000, 100, 10]\n\n[[ivfpq]]",
                                                    "cohorts_permille = [1000, 5]\n\n[[ivfpq]]"),
            "duplicate values": CONFIG.replace("m = [4, 8]", "m = [4, 4]"),
            "permille out of range": CONFIG.replace("cohorts_permille = [1000, 100, 10]\n\n[measure]",
                                                    "cohorts_permille = [1000, 100, 10, 2000]\n\n[measure]"),
            "duplicate name": CONFIG.replace('name = "lowmem"', 'name = "grid"'),
            "bad nbits": CONFIG.replace("nbits = 4", "nbits = 17"),
        }
        for name, text in cases.items():
            with self.subTest(case=name), self.assertRaises(SweepError):
                parse_sweep(text.encode())


class FaissBuildTests(unittest.TestCase):
    """Small builds: they check the pipeline and its invariants, not ANN quality."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(close_opened)
        self.root = Path(directory.name)
        rng = np.random.default_rng(21)
        self.documents = write(self.root / "documents", [f"d{index:04d}" for index in range(800)],
                               unit(rng.standard_normal((800, 16))), shard_rows=128)
        self.queries = write(self.root / "queries", [f"q{index:02d}" for index in range(30)],
                             unit(rng.standard_normal((30, 16))), role="query", shard_rows=16)
        self.config = parse_sweep(CONFIG.encode())
        buckets = cohort_buckets(self.documents.ids)
        self.masks, self.oracles = {}, {}
        for permille in (1000, 100, 10):
            mask = cohort_mask(buckets, permille)
            eligible = np.nonzero(mask)[0].astype(np.int64)
            result = exact_top_k(self.queries, self.documents, eligible, 20, FlatSearcher(self.documents, eligible),
                                 initial=40)
            self.masks[permille], self.oracles[permille] = mask, (result.rows, result.scores)

    def run_one(self, label, name="out"):
        build = next(build for build in expand(self.config) if build.label == label)
        return run_build(build, self.documents, self.queries, self.oracles, self.masks, self.config["measure"],
                         self.root / name / label), self.root / name / label

    def points(self, directory):
        return [json.loads(line) for line in (directory / "points.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_hnsw_reaches_the_exact_list_with_a_large_beam_and_respects_the_cohort(self):
        record, directory = self.run_one("hnsw-grid-efc20-m8-s11")
        points = {(point["cohort_permille"], point["search"]["ef_search"]): point for point in self.points(directory)}
        self.assertEqual(points[(1000, 400)]["recall"]["@20"]["tie_aware"]["mean"], 1.0)
        self.assertLessEqual(points[(1000, 20)]["recall"]["@20"]["tie_aware"]["mean"], 1.0)
        # Cohort 10 holds 5 documents, fewer than the depth of 20. The 15 positions per query it cannot
        # fill are not short results. FAISS filtered HNSW still leaves some eligible documents unfound at
        # efSearch 400 (reproduced with FAISS alone: 3 empty positions in 3 queries); each lowers recall.
        eligible = int(self.masks[10].sum())
        short = points[(10, 400)]["short_results"]
        self.assertEqual(eligible, 5)
        self.assertLess(short, 30 * (20 - eligible))
        self.assertAlmostEqual(points[(10, 400)]["recall"]["@20"]["tie_aware"]["mean"], 1 - short / (30 * eligible))
        filtered = points[(100, 400)]
        self.assertGreater(filtered["recall"]["@5"]["tie_aware"]["queries"], 0)
        self.assertGreater(points[(1000, 400)]["distances_per_query"], points[(1000, 20)]["distances_per_query"])
        self.assertAlmostEqual(record["estimated_bytes"], record["estimated_bytes_per_vector"] * 800, places=6)
        # float32 rows alone take 800 x 16 x 4 bytes; level 0 adds 2 M = 16 links of 4 bytes per vector.
        self.assertGreaterEqual(record["estimated_bytes"], 800 * 16 * 4 + 800 * 16 * 4)
        per_query = np.load(directory / points[(1000, 400)]["per_query"]["file"])
        self.assertEqual(per_query.shape, (30, 4))
        self.assertEqual(completed(directory), record)

    def test_single_thread_builds_repeat_and_insertion_order_changes_the_graph(self):
        first, _ = self.run_one("hnsw-grid-efc20-m4-s11", "first")
        again, _ = self.run_one("hnsw-grid-efc20-m4-s11", "again")
        other, _ = self.run_one("hnsw-grid-efc20-m4-s29", "other")
        self.assertEqual(first["structure_sha256"], again["structure_sha256"])
        self.assertNotEqual(first["structure_sha256"], other["structure_sha256"])

    def test_ivfpq_builds_with_its_seeds_and_reports_lower_memory(self):
        record, directory = self.run_one("ivfpq-lowmem-nbits4-nlist8-pqm4-train300-tseed11-s11")
        again, _ = self.run_one("ivfpq-lowmem-nbits4-nlist8-pqm4-train300-tseed11-s11", "again")
        self.assertEqual(record["structure_sha256"], again["structure_sha256"])
        flat, _ = self.run_one("hnsw-grid-efc20-m4-s11", "flat")
        self.assertLess(record["estimated_bytes_per_vector"], flat["estimated_bytes_per_vector"])
        recalls = [point["recall"]["@20"]["tie_aware"]["mean"] for point in self.points(directory)]
        self.assertLessEqual(recalls[0], recalls[1])

    def test_a_changed_record_marks_the_build_incomplete(self):
        record, directory = self.run_one("hnsw-grid-efc20-m4-s11")
        name = next(iter(record["per_query_sha256"]))
        values = np.load(directory / name)
        values[0, 0] = 0.5
        np.save(directory / name, values)
        self.assertIsNone(completed(directory))

        record, directory = self.run_one("hnsw-grid-efc20-m4-s11", "points")
        (directory / "points.jsonl").write_bytes((directory / "points.jsonl").read_bytes().replace(b'"qps":', b'"qps": ', 1))
        self.assertIsNone(completed(directory))
        (directory / "build.json").unlink()
        self.assertIsNone(completed(directory))

    def test_an_index_that_returns_an_ineligible_document_stops_the_sweep(self):
        masks = dict(self.masks)
        masks[100] = self.masks[100].copy()
        # Claim that one document the cohort selected is not eligible: the selector still returns it.
        masks[100][np.nonzero(self.masks[100])[0][0]] = False
        build = next(build for build in expand(self.config) if build.label == "hnsw-grid-efc20-m8-s11")
        selector_mask = self.masks[100]

        import ann_sweep

        original = ann_sweep.selector
        ann_sweep.selector = lambda mask: original(selector_mask[insertion_order(800, 11)])
        try:
            with self.assertRaisesRegex(SweepError, "ineligible results"):
                run_build(build, self.documents, self.queries, self.oracles, masks, self.config["measure"],
                          self.root / "bad")
        finally:
            ann_sweep.selector = original
