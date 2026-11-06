from dataclasses import FrozenInstanceError, replace
import unittest

from search_retrieval.contracts.identity import ChunkKey, DocumentKey, RevisionKey


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.document = DocumentKey("tenant-a", "source-a", "document-a")
        self.revision = RevisionKey(self.document, 7)
        self.chunk = ChunkKey(self.revision, "transform-a", "fragment-a")

    def test_documents_with_matching_ids_stay_distinct_across_tenants_and_sources(self):
        different_tenant = replace(self.document, tenant_id="tenant-b")
        different_source = replace(self.document, source_id="source-b")

        self.assertEqual(len({self.document, different_tenant, different_source}), 3)

    def test_chunks_stay_distinct_across_revisions_and_transformations(self):
        different_revision = replace(
            self.chunk, revision=replace(self.revision, sequence=8)
        )
        different_transformation = replace(self.chunk, transformation_id="transform-b")

        self.assertEqual(len({self.chunk, different_revision, different_transformation}), 3)

    def test_equivalent_keys_deduplicate(self):
        revision = RevisionKey(DocumentKey("tenant-a", "source-a", "document-a"), 7)
        duplicate = ChunkKey(revision, "transform-a", "fragment-a")

        self.assertEqual(len({self.chunk, duplicate}), 1)

    def test_revision_key_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.revision.sequence = 8
