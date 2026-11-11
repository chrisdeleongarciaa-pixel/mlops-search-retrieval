"""Expected digests were computed with `sha256sum` over the literal bytes quoted in each test."""

from dataclasses import FrozenInstanceError, replace
import unittest

from search_retrieval.contracts.canonical import canonical_json, sealed_digest
from search_retrieval.contracts.identity import (
    ContentChunkKey, DocumentKey, GenerationKey, QuerySetKey, RevisionKey, RunKey, SpaceKey, TransformKey,
)


A, B, C = "a" * 64, "b" * 64, "c" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def space(**changes):
    values = dict(
        encoder="org/model", revision="r1", files={"model.safetensors": A},
        query_template="query: {text}", document_template="passage: {text}", pooling="masked_mean",
        normalize=True, max_tokens=512, dtype="float32", dimensions=768, mrl_dimensions=768,
        quantization="none",
    )
    return SpaceKey(**{**values, **changes})


class CanonicalTests(unittest.TestCase):
    def test_bytes_are_sorted_compact_utf8(self):
        self.assertEqual(
            canonical_json({"b": 1, "a": [True, None, "é"]}),
            b'{"a":[true,null,"\xc3\xa9"],"b":1}',
        )

    def test_sealed_digest_matches_an_external_hash(self):
        # printf '%s' '{"body":{"a":1},"kind":"transform","schema_version":1}' | sha256sum
        self.assertEqual(
            sealed_digest("transform", {"a": 1}),
            "abba38e2d594600c4bc8028b5745f99e0ac251a28fb080129e5ee55026de3a34",
        )

    def test_key_order_is_irrelevant_and_kind_and_number_type_are_not(self):
        self.assertEqual(sealed_digest("x", {"a": 1, "b": 2}), sealed_digest("x", {"b": 2, "a": 1}))
        self.assertNotEqual(sealed_digest("x", {"a": 1}), sealed_digest("y", {"a": 1}))
        self.assertNotEqual(sealed_digest("x", {"a": 1}), sealed_digest("x", {"a": 1.0}))

    def test_values_without_one_serialization_are_rejected(self):
        for value in (float("nan"), float("inf"), {1: "a"}, {"a": {2, 3}}, b"bytes", object()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_json(value)


class SealedKeyTests(unittest.TestCase):
    def test_transform_key_matches_an_external_hash(self):
        # printf '%s' '{"body":{"chunking":{"policy":"identity"},"configuration":{},"parser":"none",
        #   "parser_version":"0","tokenizer":"none"},"kind":"transform","schema_version":1}' | sha256sum
        key = TransformKey("none", "0", {}, {"policy": "identity"}, "none")
        self.assertEqual(key.digest, "cd5974eed5384f6946d48604c684ae8e4ec5a52dba2301bb455592886c8e332b")

    def test_query_set_key_matches_an_external_hash(self):
        key = QuerySetKey("q", "dev", A, B, "human")
        self.assertEqual(key.digest, "68f0dbec480715dc5c2c6690c50597eb809318e3da98b15304f9a8bcc6d0506a")
        self.assertNotEqual(key, QuerySetKey("q", "test", A, B, "human"))
        self.assertNotEqual(key, QuerySetKey("q", "dev", A, B, "llm"))

    def test_every_space_member_changes_the_space(self):
        base = space()
        changes = dict(
            encoder="org/other", revision="r2", files={"model.safetensors": B},
            query_template="task: search | query: {text}", document_template="{text}",
            pooling="cls", normalize=False, max_tokens=256, dtype="float16", dimensions=1024,
            mrl_dimensions=256, quantization="int8",
        )
        digests = {space(**{name: value}).digest for name, value in changes.items()}
        self.assertEqual(len(digests), len(changes))
        self.assertNotIn(base.digest, digests)
        self.assertEqual(base, space())
        self.assertEqual(len({base, space()}), 1)

    def test_space_rejects_incomplete_or_impossible_descriptions(self):
        for change in (
            {"files": {}}, {"files": {"model.safetensors": "ABC"}}, {"query_template": "query: "},
            {"mrl_dimensions": 1024}, {"dimensions": 0}, {"normalize": 1}, {"max_tokens": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                space(**change)

    def test_content_chunk_key_binds_revision_transform_position_and_content(self):
        revision = RevisionKey(DocumentKey("tenant", "source", "doc"), 3)
        chunk = ContentChunkKey(revision, A, 0, 0, 10, B)
        variants = {
            ContentChunkKey(replace(revision, sequence=4), A, 0, 0, 10, B),
            ContentChunkKey(RevisionKey(DocumentKey("other", "source", "doc"), 3), A, 0, 0, 10, B),
            ContentChunkKey(revision, C, 0, 0, 10, B), ContentChunkKey(revision, A, 1, 0, 10, B),
            ContentChunkKey(revision, A, 0, 0, 11, B), ContentChunkKey(revision, A, 0, 0, 10, C),
        }
        self.assertEqual(len(variants | {chunk}), 7)
        for arguments in ((revision, A, -1, 0, 10, B), (revision, A, 0, 10, 10, B), (revision, "x", 0, 0, 1, B)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ContentChunkKey(*arguments)

    def test_generation_holds_a_set_of_spaces_and_its_parameters(self):
        one = GenerationKey(A, B, (A, C), "vespa", "s1", {"hnsw": {"max_links_per_node": 16}}, 11)
        self.assertNotEqual(one, GenerationKey(A, B, (A, C), "vespa", "s1", {"hnsw": {"max_links_per_node": 32}}, 11))
        self.assertNotEqual(one, GenerationKey(A, B, (A, C), "vespa", "s1", {"hnsw": {"max_links_per_node": 16}}, 29))
        self.assertNotEqual(one, GenerationKey(A, B, (A,), "vespa", "s1", {"hnsw": {"max_links_per_node": 16}}, 11))
        for spaces in ((C, A), (A, A), [A, C]):
            with self.subTest(spaces=spaces), self.assertRaises(ValueError):
                GenerationKey(A, B, spaces, "vespa", "s1", {}, 11)

    def test_run_key_changes_with_each_member_and_requires_a_full_commit(self):
        base = dict(generation=A, ranking_profile="dense", target_hits=1000, fusion={"kind": "none"},
                    query_set=B, code_commit=COMMIT, runtime=C)
        changes = dict(generation=B, ranking_profile="lexical", target_hits=100,
                       fusion={"kind": "rrf", "k": 60}, query_set=C, code_commit="f" * 40, runtime=A)
        digests = {RunKey(**{**base, name: value}).digest for name, value in changes.items()}
        self.assertEqual(len(digests | {RunKey(**base).digest}), len(changes) + 1)
        for change in ({"code_commit": "e1b23a1"}, {"fusion": {}}, {"target_hits": 0}, {"runtime": "x"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                RunKey(**{**base, **change})

    def test_sealed_keys_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            space().pooling = "cls"
