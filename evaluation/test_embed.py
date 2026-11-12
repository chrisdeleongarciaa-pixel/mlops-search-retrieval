"""Batch embedding: rendering, snapshot checks, resumable encoding and the space registry."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from search_retrieval.adapters.e5 import E5Space
from search_retrieval.adapters.embeddinggemma import EmbeddingGemmaSpace
from search_retrieval.adapters.space_encoder import render, verify_snapshot
from search_retrieval.experiment.run_config import ConfigError, read_space
from embed import ROOT, SyntheticEncoder, document_fields, encode_set, parse_operation
from fetch_model import fetch, file_url
from mteb_gate import PUBLISHED, judge, select
from vector_store import Identity, VectorSetWriter, VectorStoreError, verify_vector_set


A, B = "a" * 64, "b" * 64


class RenderingTests(unittest.TestCase):
    def test_templates_render_as_the_model_cards_write_them(self):
        self.assertEqual(render("query: {text}", "", "RNA"), "query: RNA")
        self.assertEqual(render("title: {title or none} | text: {text}", "", "abstract"), "title: none | text: abstract")
        self.assertEqual(render("title: {title or none} | text: {text}", " Cell ", "abstract"), "title: Cell | text: abstract")
        self.assertEqual(render("Instruct: task\nQuery:{text}", "", "claim"), "Instruct: task\nQuery:claim")
        self.assertEqual(render("{text}", "ignored", "passage"), "passage")

    def test_each_transform_hands_the_template_its_own_fields(self):
        self.assertEqual(document_fields("title-space-text", "T", "body"), ("", "T body"))
        self.assertEqual(document_fields("title-space-text", "", "body"), ("", " body"))
        self.assertEqual(document_fields("title-text-fields", "T", "body"), ("T", "body"))
        self.assertEqual(document_fields("passage", "T", "body"), ("", "body"))
        with self.assertRaises(ConfigError):
            document_fields("sections", "T", "body")

    def test_operations_parse_strictly(self):
        self.assertEqual(parse_operation("mrl:256"), {"kind": "mrl", "dimensions": 256})
        self.assertEqual(parse_operation("binary"), {"kind": "binary"})
        for text in ("mrl", "mrl:x", "binary:1", "pq"):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_operation(text)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_must_match_every_pinned_file_and_hold_nothing_else(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sub").mkdir()
            (root / "config.json").write_bytes(b"{}")
            (root / "sub/weights").write_bytes(b"w")
            (root / "README.md").write_bytes(b"optional")
            files = {"config.json": sha256(b"{}").hexdigest(), "sub/weights": sha256(b"w").hexdigest()}
            self.assertEqual(verify_snapshot(root, files), root.resolve())
            with self.assertRaisesRegex(ValueError, "does not match the space"):
                verify_snapshot(root, {**files, "sub/weights": A})
            with self.assertRaisesRegex(ValueError, "clean, complete snapshot"):
                verify_snapshot(root, {**files, "tokenizer.json": A})
            (root / "extra.bin").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "clean, complete snapshot"):
                verify_snapshot(root, files)


class RegistryTests(unittest.TestCase):
    def test_registered_spaces_keep_the_identifiers_of_the_archived_evidence(self):
        evidence = json.loads((ROOT / "evaluation/evidence/scifact-gemma-exact-cpu/manifest.json").read_bytes())
        e5, _ = read_space(ROOT, "manifests/space/multilingual-e5-base-fp32.json")
        self.assertEqual(e5["legacy_id"], evidence["encoding_profiles"]["e5"]["space_id"])
        self.assertEqual(E5Space(e5["revision"], e5["files"]).id, e5["legacy_id"])
        gemma, _ = read_space(ROOT, "manifests/space/embeddinggemma-300m-fp32.json")
        source = json.loads((ROOT / "config/embeddinggemma-300m.json").read_bytes())
        self.assertEqual(gemma["legacy_id"], evidence["encoding_profiles"]["gemma"]["space_id"])
        self.assertEqual(EmbeddingGemmaSpace(gemma["revision"], source["git_blobs"], source["lfs_sha256"]).id,
                         gemma["legacy_id"])
        self.assertEqual(gemma["files"], evidence["encoding_profiles"]["gemma"]["files"])

    def test_every_registered_space_is_distinct_and_names_an_adapter(self):
        spaces = {path.name: read_space(ROOT, path.relative_to(ROOT).as_posix())
                  for path in sorted((ROOT / "manifests/space").glob("*.json"))}
        self.assertGreaterEqual(len(spaces), 5)
        self.assertEqual(len({key.digest for _, key in spaces.values()}), len(spaces))
        for name, (body, key) in spaces.items():
            with self.subTest(space=name):
                self.assertIn(body["adapter"], ("transformers-masked-mean", "sentence-transformers"))
                self.assertEqual((key.quantization, key.mrl_dimensions), ("none", key.dimensions))
        fp32, fp16 = spaces["embeddinggemma-300m-fp32.json"][1], spaces["embeddinggemma-300m-fp16.json"][1]
        self.assertEqual((fp32.files, fp32.query_template, fp32.dtype, fp16.dtype),
                         (fp16.files, fp16.query_template, "float32", "float16"))


class EncodeSetTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.body, self.space = read_space(ROOT, "evaluation/fixtures/synthetic-space-float32.json")
        self.identity = Identity(self.space, "document", {"corpus": A, "transform": B})
        # Lengths are not monotonic in collection order, so batching by length must restore the order.
        self.items = [(f"d{index:02d}", "T" if index % 2 else "", "word " * ((index * 7 % 11) * 40 + 1))
                      for index in range(11)]

    def run_encode(self, directory, items, **options):
        writer = VectorSetWriter(directory, self.identity, shard_rows=4)
        stats = encode_set(writer, items, SyntheticEncoder(self.body), self.body["doc_template"], batch_size=3, **options)
        return writer, stats

    def test_rows_follow_collection_order_and_truncation_is_counted(self):
        writer, stats = self.run_encode(self.root / "set", self.items)
        sealed = writer.finish(11)
        self.assertEqual((stats["encoded_rows"], stats["resumed_from_rows"], sealed["body"]["shards"]), (11, 0, 3))
        # "title: X | text: " adds four tokens; texts of 40k+1 words pass 256 tokens for k = 7, 8, 9, 10.
        self.assertEqual(sealed["body"]["truncated"], 4)
        encoder = SyntheticEncoder(self.body)
        expected = encoder.encode_texts([f"title: {title or 'none'} | text: {text}" for _, title, text in self.items])
        stored = np.concatenate([np.load(self.root / "set" / f"shard-{ordinal:05d}.npy") for ordinal in range(3)])
        np.testing.assert_array_equal(stored, expected)

    def test_interrupted_encoding_resumes_without_encoding_stored_rows_again(self):
        complete, _ = self.run_encode(self.root / "complete", self.items)
        reference = complete.finish(11)

        def interrupted():
            yield from self.items[:9]
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.run_encode(self.root / "set", interrupted())
        writer, stats = self.run_encode(self.root / "set", self.items)
        self.assertEqual((stats["resumed_from_rows"], stats["encoded_rows"]), (8, 3))
        self.assertEqual(writer.finish(11), reference)
        self.assertEqual(verify_vector_set(self.root / "set"), reference)

    def test_resume_detects_a_changed_or_shorter_collection(self):
        with self.assertRaises(KeyboardInterrupt):
            def interrupted():
                yield from self.items[:9]
                raise KeyboardInterrupt
            self.run_encode(self.root / "set", interrupted())
        reordered = [self.items[1], self.items[0], *self.items[2:]]
        with self.assertRaisesRegex(VectorStoreError, "collection changed"):
            self.run_encode(self.root / "set", reordered)
        with self.assertRaisesRegex(VectorStoreError, "shorter than the existing shards"):
            self.run_encode(self.root / "set", self.items[:4])

    def test_synthetic_encoder_is_refused_for_real_spaces(self):
        real, _ = read_space(ROOT, "manifests/space/multilingual-e5-base-fp32.json")
        with self.assertRaises(ConfigError):
            SyntheticEncoder(real)


class GateTests(unittest.TestCase):
    def test_tolerance_is_in_points_and_symmetric(self):
        self.assertTrue(judge(0.7014, 0.6965, 0.5)["passed"])
        self.assertTrue(judge(0.6916, 0.6965, 0.5)["passed"])
        self.assertFalse(judge(0.7016, 0.6965, 0.5)["passed"])
        self.assertFalse(judge(0.6914, 0.6965, 0.5)["passed"])
        self.assertAlmostEqual(judge(0.78871, 0.78675, 0.5)["difference_points"], 0.196, places=9)

    def test_every_published_check_names_a_registered_space_and_its_source(self):
        published = json.loads(PUBLISHED.read_bytes())
        self.assertEqual(published["tolerance_points"], 0.5)
        self.assertEqual(len(published["source"]["commit"]), 40)
        names = [check["name"] for check in published["checks"]]
        self.assertEqual(len(set(names)), len(names))
        for check in published["checks"]:
            with self.subTest(check=check["name"]):
                body, _ = read_space(ROOT, check["space"])
                self.assertEqual(select(check["name"]), (check, 0.5))
                self.assertIn(check["published_model_revision"], check["published_path"])
                self.assertTrue(0 < check["published"] < 1)
                self.assertIn(body["encoder"].split("/")[1].lower(), check["published_path"].lower())
        with self.assertRaises(ValueError):
            select("unknown")


class FetchTests(unittest.TestCase):
    def test_urls_pin_the_revision_and_reject_paths_that_leave_the_repository(self):
        self.assertEqual(file_url("org/model", "abc", "1_Pooling/config.json"),
                         "https://huggingface.co/org/model/resolve/abc/1_Pooling/config.json")
        for encoder, name in (("model", "config.json"), ("org/model", "../x"), ("org/model", "/etc/x")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                file_url(encoder, "abc", name)

    def test_a_verified_file_is_kept_and_a_wrong_download_is_discarded(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.bin").write_bytes(b"weights")
            url = (root / "source.bin").as_uri()
            good = sha256(b"weights").hexdigest()
            self.assertTrue(fetch(url, root / "model/weights.bin", good, None))
            self.assertFalse(fetch("file:///nonexistent", root / "model/weights.bin", good, None))
            with self.assertRaisesRegex(ValueError, "does not match the space"):
                fetch(url, root / "model/other.bin", A, None)
            self.assertEqual(sorted(path.name for path in (root / "model").iterdir()), ["weights.bin"])
