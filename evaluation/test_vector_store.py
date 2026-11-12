"""Shard store, derived forms and float16 parity.

Expected hashes come from `sha256sum` over literal bytes. Packed-sign expectations are the
examples in the Vespa binarization guide. Other values are calculated by hand.
"""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from search_retrieval.contracts.identity import SpaceKey
from search_retrieval.experiment.manifests import seal
from vector_store import (
    BINARY, INT8, Identity, VectorSetWriter, VectorStoreError, derive, derived_space, ids_bytes,
    in_parity_sample, iterate_shards, pack_signs, parity_report, read_shard, round_int8, row_bytes,
    stored_width, top_k, truncate_mrl, verify_vector_set,
)


A, B, C = "a" * 64, "b" * 64, "c" * 64


def space(**changes) -> SpaceKey:
    values = dict(
        encoder="synthetic/test", revision="r1", files={"weights": A}, query_template="q: {text}",
        document_template="{text}", pooling="none", normalize=True, max_tokens=8, dtype="float32",
        dimensions=16, mrl_dimensions=16, quantization="none",
    )
    return SpaceKey(**{**values, **changes})


def unit_rows(count: int, width: int = 16, seed: int = 7) -> np.ndarray:
    rows = np.random.default_rng(seed).standard_normal((count, width))
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


def documents(key: SpaceKey | None = None) -> Identity:
    return Identity(key or space(), "document", {"corpus": A, "transform": B})


def write_set(directory: Path, rows: np.ndarray, *, shard_rows: int, identity: Identity | None = None,
              parity_every: int | None = None, stop_after: int | None = None):
    writer = VectorSetWriter(directory, identity or documents(), shard_rows=shard_rows, parity_every=parity_every)
    ids = [f"d{index:03d}" for index in range(len(rows))]
    for start in range(writer.rows, len(rows), shard_rows):
        if stop_after is not None and len(writer.shards) == stop_after:
            return writer
        writer.append(ids[start:start + shard_rows], rows[start:start + shard_rows], truncated=1 if start == 0 else 0)
    return writer.finish(len(rows))


class EncodingTests(unittest.TestCase):
    def test_row_and_id_bytes_match_external_hashes(self):
        # printf '\x00\x00\x80\x3f\x00\x00\x00\x00' | sha256sum   (float32 1.0, 0.0, little-endian)
        self.assertEqual(sha256(row_bytes(np.array([[1.0, 0.0]], dtype=np.float32), "float32")).hexdigest(),
                         "434b26042aff3fb844a4c4c6be0d81a079b0ce84cfb8190679024404e5dc4822")
        # printf '\x00\x3c\x00\x00' | sha256sum                   (float16 1.0, 0.0)
        self.assertEqual(sha256(row_bytes(np.array([[1.0, 0.0]], dtype=np.float32), "float16")).hexdigest(),
                         "487d509ea66df48be4c1c0ed403634de9a44f4bf554f5a226c1803aa51bbe61b")
        # printf 'a\nb\n' | sha256sum
        self.assertEqual(sha256(ids_bytes(["a", "b"])).hexdigest(),
                         "911169ddaaf146aff539f58c26c489af3b892dff0fe283c1c264c65ae5aa59a2")
        for ids in ([""], ["a\nb"], [" a"]):
            with self.subTest(ids=ids), self.assertRaises(VectorStoreError):
                ids_bytes(ids)

    def test_packed_signs_match_the_vespa_examples(self):
        rows = np.array([[1, 1, 1, 1, 1, 1, 1, 1], [0, 0, 0, 0, 0, 0, 0, 0], [-1, -1, -1, -1, -1, -1, -1, -1],
                         [1, 0, 0, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0, 0, 1]], dtype=np.float32)
        self.assertEqual(pack_signs(rows).tolist(), [[-1], [0], [0], [-128], [-127]])
        self.assertEqual(pack_signs(np.array([[.1, -.2, .3, .4, -.5, -.6, .7, .8] * 2], dtype=np.float32)).tolist(),
                         [[0b10110011 - 256] * 2])

    def test_int8_rounding_and_mrl_truncation_match_hand_calculation(self):
        rows = np.array([[1.0, -1.0, 0.5, 0.0, 0.004, -0.5]], dtype=np.float32)
        # 0.5 * 127 = 63.5 rounds half to even, to 64; 0.004 * 127 = 0.508 rounds to 1.
        self.assertEqual(round_int8(rows).tolist(), [[127, -127, 64, 0, 1, -64]])
        truncated = truncate_mrl(np.array([[0.6, 0.8, 0.0, 0.0], [0.5, 0.5, 0.5, 0.5]], dtype=np.float32), 2)
        np.testing.assert_allclose(truncated, [[0.6, 0.8], [2 ** -0.5, 2 ** -0.5]], atol=1e-7)
        with self.assertRaisesRegex(VectorStoreError, "zero length"):
            truncate_mrl(np.array([[0.0, 0.0, 1.0, 0.0]], dtype=np.float32), 2)

    def test_exact_ranking_breaks_ties_by_document_id(self):
        docs = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        self.assertEqual(top_k(np.array([[1.0, 0.0]], dtype=np.float32), docs, ["z", "a", "m"], 3), [["a", "z", "m"]])


class SpaceFormTests(unittest.TestCase):
    def test_derived_spaces_change_only_numeric_form(self):
        parent = space()
        mrl = derived_space(parent, {"kind": "mrl", "dimensions": 8})
        binary = derived_space(mrl, {"kind": "binary"})
        rounded = derived_space(parent, {"kind": "int8"})
        self.assertEqual((mrl.mrl_dimensions, mrl.dtype, stored_width(mrl)), (8, "float32", 8))
        self.assertEqual((binary.quantization, binary.dtype, stored_width(binary)), (BINARY, "int8", 1))
        self.assertEqual((rounded.quantization, stored_width(rounded)), (INT8, 16))
        self.assertEqual(len({parent, mrl, binary, rounded}), 4)
        for derived in (mrl, binary, rounded):
            self.assertEqual((derived.encoder, derived.revision, derived.files, derived.query_template),
                             (parent.encoder, parent.revision, parent.files, parent.query_template))

    def test_invalid_derivations_and_forms_are_rejected(self):
        parent = space()
        for operation in ({"kind": "mrl", "dimensions": 16}, {"kind": "mrl", "dimensions": 0}, {"kind": "mrl"},
                          {"kind": "binary", "threshold": 1}, {"kind": "pq"}):
            with self.subTest(operation=operation), self.assertRaises(VectorStoreError):
                derived_space(parent, operation)
        with self.assertRaisesRegex(VectorStoreError, "not a source"):
            derived_space(derived_space(parent, {"kind": "binary"}), {"kind": "int8"})
        with self.assertRaisesRegex(VectorStoreError, "divisible by eight"):
            stored_width(space(mrl_dimensions=12, dtype="int8", quantization=BINARY))
        for key in (space(dtype="float64"), space(dtype="float32", quantization=BINARY), space(quantization="pq")):
            with self.subTest(key=key.dtype), self.assertRaises(VectorStoreError):
                documents(key)
        with self.assertRaises(VectorStoreError):
            Identity(space(), "query", {"corpus": A, "transform": B})


class VectorSetTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.rows = unit_rows(5)

    def test_set_hashes_do_not_depend_on_shard_boundaries(self):
        small = write_set(self.root / "small", self.rows, shard_rows=2)
        large = write_set(self.root / "large", self.rows, shard_rows=5)
        self.assertEqual((small["body"]["shards"], large["body"]["shards"]), (3, 1))
        for name in ("rows_sha256", "ids_sha256", "rows", "truncated", "identity"):
            self.assertEqual(small["body"][name], large["body"][name])
        self.assertEqual(small["body"]["rows_sha256"], sha256(row_bytes(self.rows, "float32")).hexdigest())
        self.assertEqual(small["body"]["truncated"], 1)
        self.assertEqual(verify_vector_set(self.root / "small"), small)
        stored = np.concatenate([rows for _, rows, _ in iterate_shards(self.root / "small")])
        np.testing.assert_array_equal(stored, self.rows)

    def test_an_interrupted_write_resumes_to_the_same_set(self):
        complete = write_set(self.root / "complete", self.rows, shard_rows=2)
        target = self.root / "resumed"
        write_set(target, self.rows, shard_rows=2, stop_after=2)
        # A crash after the rows of the third shard were written but before its manifest.
        np.save(target / "shard-00002.npy", self.rows[4:])
        (target / "shard-00002.ids.tmp").write_bytes(b"partial")
        writer = VectorSetWriter(target, documents(), shard_rows=2)
        self.assertEqual(writer.rows, 4)
        self.assertEqual(sorted(path.name for path in target.glob("shard-00002*")), [])
        self.assertEqual(write_set(target, self.rows, shard_rows=2), complete)

    def test_resume_rejects_another_identity_or_shard_size(self):
        target = self.root / "set"
        write_set(target, self.rows, shard_rows=2, stop_after=1)
        other = Identity(space(), "document", {"corpus": C, "transform": B})
        with self.assertRaisesRegex(VectorStoreError, "another vector set"):
            VectorSetWriter(target, other, shard_rows=2)
        with self.assertRaisesRegex(VectorStoreError, "shard size"):
            VectorSetWriter(target, documents(), shard_rows=3)
        write_set(target, self.rows, shard_rows=2)
        with self.assertRaisesRegex(VectorStoreError, "already complete"):
            VectorSetWriter(target, documents(), shard_rows=2)

    def test_changed_missing_and_extra_shards_fail_verification(self):
        target = self.root / "set"
        write_set(target, self.rows, shard_rows=2)
        changed = self.rows[:2].copy()
        changed[0, 0] += 0.25
        np.save(target / "shard-00000.npy", changed)
        with self.assertRaisesRegex(VectorStoreError, "does not match its manifest"):
            verify_vector_set(target)
        np.save(target / "shard-00000.npy", self.rows[:2])
        verify_vector_set(target)
        (target / "shard-00001.ids").write_bytes(b"d002\nd999\n")
        with self.assertRaisesRegex(VectorStoreError, "does not match its manifest"):
            verify_vector_set(target)
        (target / "shard-00001.ids").write_bytes(b"d002\nd003\n")
        (target / "shard-00003.json").write_bytes((target / "shard-00002.json").read_bytes())
        with self.assertRaises(VectorStoreError):
            verify_vector_set(target)
        (target / "shard-00003.json").unlink()
        (target / "shard-00001.json").unlink()
        with self.assertRaises(VectorStoreError):
            verify_vector_set(target)

    def test_invalid_rows_are_rejected_before_anything_is_written(self):
        writer = VectorSetWriter(self.root / "set", documents(), shard_rows=4)
        long_rows = self.rows[:2] * 1.001
        cases = (
            (["a", "b"], long_rows, 0), (["a", "a"], self.rows[:2], 0), (["a"], self.rows[:2], 0),
            (["a", "b"], self.rows[:2].astype(np.float64), 0), (["a", "b"], self.rows[:2, :8], 0),
            (["a", "b"], np.full((2, 16), np.nan, dtype=np.float32), 0), (["a", "b"], self.rows[:2], 3),
            ([f"d{index}" for index in range(5)], self.rows, 0),
        )
        for ids, rows, truncated in cases:
            with self.subTest(ids=ids, truncated=truncated), self.assertRaises(VectorStoreError):
                writer.append(ids, rows, truncated)
        self.assertEqual(list((self.root / "set").iterdir()), [])
        writer.append(["a", "b"], self.rows[:2], 0)
        with self.assertRaisesRegex(VectorStoreError, "short shard ends"):
            writer.append(["c"], self.rows[2:3], 0)
        with self.assertRaisesRegex(VectorStoreError, "expected 5"):
            writer.finish(5)

    def test_derived_sets_come_from_stored_rows_and_record_their_parent(self):
        parent = write_set(self.root / "parent", self.rows, shard_rows=2)
        binary = derive(self.root / "parent", self.root / "binary", space(), {"kind": "binary"})
        self.assertEqual(binary["body"]["derived_from"], {"vector_set": parent["sha256"], "operation": {"kind": "binary"}})
        self.assertEqual(binary["body"]["identity"]["space"], derived_space(space(), {"kind": "binary"}).digest)
        self.assertEqual((binary["body"]["identity"]["width"], binary["body"]["ids_sha256"], binary["body"]["truncated"]),
                         (2, parent["body"]["ids_sha256"], 1))
        packed = np.concatenate([rows for _, rows, _ in iterate_shards(self.root / "binary")])
        np.testing.assert_array_equal(packed, pack_signs(self.rows))
        mrl = derive(self.root / "parent", self.root / "mrl", space(), {"kind": "mrl", "dimensions": 8})
        shortened = np.concatenate([rows for _, rows, _ in iterate_shards(self.root / "mrl")])
        np.testing.assert_allclose(np.linalg.norm(shortened, axis=1), 1, atol=1e-6)
        np.testing.assert_allclose(shortened, truncate_mrl(self.rows, 8), atol=0)
        self.assertEqual(mrl["body"]["shards"], 3)
        with self.assertRaisesRegex(VectorStoreError, "another space"):
            derive(self.root / "parent", self.root / "wrong", space(revision="r2"), {"kind": "binary"})


class ParityTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.half = space(dtype="float16")
        self.rows = unit_rows(64, seed=11)
        self.queries = unit_rows(6, seed=13)
        writer = VectorSetWriter(self.root / "queries", Identity(self.half, "query", {"queries": C}),
                                 shard_rows=6, parity_every=1)
        writer.append([f"q{index}" for index in range(6)], self.queries, 0)
        writer.finish(6)

    def test_sampling_rule_is_fixed_by_the_id(self):
        chosen = [item for item in (f"d{index:03d}" for index in range(2000)) if in_parity_sample(item, 10)]
        self.assertEqual(chosen, [item for item in (f"d{index:03d}" for index in range(2000)) if in_parity_sample(item, 10)])
        self.assertTrue(150 < len(chosen) < 250)
        self.assertTrue(all(in_parity_sample(f"d{index}", 1) for index in range(50)))

    def test_float16_storage_keeps_cosine_and_top_ten_on_the_sample(self):
        write_set(self.root / "documents", self.rows, shard_rows=20, identity=documents(self.half), parity_every=2)
        report = parity_report(self.root / "documents", self.root / "queries")
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["min_cosine"], 0.9999)
        self.assertEqual((report["identical_top_k"], report["queries"], report["sample_every"]), (6, 6, 2))
        expected = sum(in_parity_sample(f"d{index:03d}", 2) for index in range(64))
        self.assertEqual(report["sample_rows"], expected)

    def test_a_degraded_store_fails_parity(self):
        target = self.root / "documents"
        write_set(target, self.rows, shard_rows=64, identity=documents(self.half), parity_every=1)
        body, ids, stored, _ = read_shard(target, 0)
        # Simulate a lossy store: swap two stored rows while the float32 sample keeps the originals.
        swapped = stored.copy()
        swapped[[0, 1]] = swapped[[1, 0]]
        np.save(target / "shard-00000.npy", swapped)
        body["rows_sha256"] = sha256(row_bytes(swapped, "float16")).hexdigest()
        (target / "shard-00000.json").write_text(json.dumps(seal("vector-shard", body)), encoding="utf-8")
        manifest = json.loads((target / "manifest.json").read_bytes())["body"]
        manifest["rows_sha256"] = body["rows_sha256"]
        (target / "manifest.json").write_text(json.dumps(seal("vector-set", manifest)), encoding="utf-8")
        report = parity_report(target, self.root / "queries")
        self.assertFalse(report["passed"])
        self.assertLess(report["min_cosine"], 0.9999)

    def test_float32_sets_keep_no_sample_and_float16_sets_require_one(self):
        with self.assertRaisesRegex(VectorStoreError, "parity sample"):
            VectorSetWriter(self.root / "a", documents(self.half), shard_rows=4)
        with self.assertRaisesRegex(VectorStoreError, "parity sample"):
            VectorSetWriter(self.root / "b", documents(), shard_rows=4, parity_every=2)
        write_set(self.root / "full", self.rows, shard_rows=64)
        with self.assertRaisesRegex(VectorStoreError, "keeps no parity sample"):
            parity_report(self.root / "full", self.root / "queries")
