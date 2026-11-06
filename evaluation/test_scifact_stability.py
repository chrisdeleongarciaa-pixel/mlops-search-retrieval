from hashlib import sha1, sha256
import gzip
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from search_retrieval.adapters.embeddinggemma import EmbeddingGemmaSpace, format_inputs
from scifact_stability import ORIGINAL_E5_AUDIT, PROTOCOL, PROTOCOL_PATH, SPACE, aggregate, bucket, digest, ordered_ids, read_test


class StabilityTests(unittest.TestCase):
    def test_deterministic_orders_preserve_exactly_the_same_eligible_ids(self):
        ids = [str(i) for i in range(5183)]
        permutations = [ordered_ids(ids, seed) for seed in PROTOCOL["insertion_order_seeds"]]
        self.assertEqual(len({tuple(order) for order in permutations}), 4)
        for seed, order in zip(PROTOCOL["insertion_order_seeds"], permutations, strict=True):
            self.assertEqual(ordered_ids(reversed(ids), seed), order)
            self.assertEqual(set(order), set(ids))
        one, ten, fifty = ({doc for doc in ids if bucket(doc) < cut} for cut in (1, 10, 50))
        self.assertTrue(one < ten < fifty < set(ids))

    def test_aggregate_uses_sample_deviation_of_four_seed_level_percentiles(self):
        def manifest(seed, latency):
            metrics = {"queries": 300, "ann_recall_at_10": 1.0, "ann_overlap_at_100": .99,
                       "engine_client_ms": {"p95": latency}}
            return {
                "model": PROTOCOL["models"][0], "space": "e5", "seed": seed,
                "corpus_vectors_sha256": "a", "train_vectors_sha256": "b", "test_vectors_sha256": "c",
                "insertion_ids_sha256": str(seed), "schema_sha256": "d", "vespa_compose_sha256": "e",
                "protocol_sha256": digest(PROTOCOL_PATH), "cohorts": {}, "exact_parity_passed": True,
                "engine_host": {"identity_sha256": "fixture"}, "encoding_profile": {"device": "cpu"},
                "query_ids_sha256": "fixture",
                "queries_per_cohort": 300, "observations": 1200, "metrics": {},
                "groups": {str(cut): {f"lt_{cut}/hnsw": metrics} for cut in PROTOCOL["cohorts_percent"]},
            }

        runs = [manifest(seed, latency) for seed, latency in zip(PROTOCOL["insertion_order_seeds"], (10, 20, 10, 20), strict=True)]
        report = aggregate(runs)["models"][PROTOCOL["models"][0]]["estimates"]["lt_10/hnsw"]
        self.assertEqual(report["observations"], 1200)
        self.assertEqual(report["p95_ms"]["mean"], 15)
        self.assertAlmostEqual(report["p95_ms"]["sample_stddev"], 5.7735026919)
        with self.assertRaisesRegex(ValueError, "independent"):
            aggregate(runs[:3])

    def test_embeddinggemma_uses_published_prompt_forms_and_validates_files(self):
        self.assertEqual(format_inputs(["DNA"], "query"), ["task: search result | query: DNA"])
        self.assertEqual(format_inputs([("", "RNA"), ("Title", "DNA")], "passage"),
                         ["title: none | text: RNA", "title: Title | text: DNA"])
        metadata, weights = b"{}", b"model"
        space = EmbeddingGemmaSpace("fixture", {
            "config.json": sha1(b"blob 2\0" + metadata, usedforsecurity=False).hexdigest(),
        }, {"model.safetensors": sha256(weights).hexdigest()})
        with TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "config.json").write_bytes(metadata)
            (root / "model.safetensors").write_bytes(weights)
            self.assertEqual(space.verify(root)[1]["config.json"], sha256(metadata).hexdigest())
            (root / "model.safetensors").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                space.verify(root)

    def test_original_e5_manifest_binds_test_via_the_archived_audit(self):
        evidence = ORIGINAL_E5_AUDIT.parent
        with TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            for name in ("manifest.json", "freeze.json"):
                (root / name).write_bytes((evidence / name).read_bytes())
            (root / "test.json").write_bytes(gzip.decompress((evidence / "test.json.gz").read_bytes()))
            with patch("scifact_stability.read_vectors", return_value=([], "verified")):
                self.assertEqual(read_test(root, {}, SPACE.id), "verified")
            (root / "test.json").write_bytes((root / "test.json").read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "provenance"):
                read_test(root, {}, SPACE.id)


if __name__ == "__main__":
    unittest.main()
