import hashlib
import io
import unittest
from unittest.mock import Mock

from search_retrieval.ingestion.accept_revision import (
    AcceptanceLedger,
    AuthorizedSource,
    DependencyFailure,
    Failure,
    OriginalStore,
    OutcomeUnknown,
    RevisionAcceptance,
)


class AcceptanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Mock(spec=AcceptanceLedger)
        self.ledger.resolve.return_value = None
        self.objects = Mock(spec=OriginalStore)
        self.service = RevisionAcceptance(self.ledger, self.objects, max_original_bytes=4)
        self.scope = AuthorizedSource("tenant", "source")
        self.envelope = {
            "schema_version": 1,
            "document_id": "doc",
            "source_position": 7,
            "operation": "UPSERT",
            "original_sha256": hashlib.sha256(b"abcd").hexdigest(),
        }

    def accept(self, envelope, original=b"abcd"):
        return self.service.accept_revision(self.scope, envelope, io.BytesIO(original))

    def test_av01_ac01_ac05_rejects_invalid_envelopes_before_effects(self):
        for patch in (
            {"schema_version": 2},
            {"schema_version": True},
            {"document_id": " "},
            {"document_id": "bad\x00id"},
            {"document_id": "\ud800"},
            {"source_position": True},
            {"source_position": 0},
            {"source_position": 2**63},
            {"operation": "WITHDRAW"},
            {"original_sha256": "g" * 64},
        ):
            with self.subTest(patch=patch):
                self.assertEqual(
                    self.accept(self.envelope | patch), Failure.INVALID_ENVELOPE
                )
        self.ledger.resolve.assert_not_called()
        self.ledger.record.assert_not_called()
        self.objects.put_verified.assert_not_called()

    def test_av01_ac01_ac05_reads_only_limit_plus_one(self):
        original = io.BytesIO(b"abcdefgh")
        result = self.service.accept_revision(self.scope, self.envelope, original)
        self.assertEqual(result, Failure.INVALID_ENVELOPE)
        self.assertEqual(original.tell(), 5)
        self.ledger.resolve.assert_not_called()

    def test_av01_ac01_ac05_rejects_scope_override_before_read(self):
        original = io.BytesIO(b"abcd")
        result = self.service.accept_revision(
            self.scope, self.envelope | {"tenant_id": "other"}, original
        )
        self.assertEqual(result, Failure.NOT_AUTHORIZED)
        self.assertEqual(original.tell(), 0)
        self.ledger.resolve.assert_not_called()
        self.objects.put_verified.assert_not_called()

    def test_av02_ac01_ac05_verifies_bytes_before_resolving_a_replay(self):
        self.assertEqual(self.accept(self.envelope, b"wxyz"), Failure.INTEGRITY_ERROR)
        self.ledger.resolve.assert_not_called()
        self.objects.put_verified.assert_not_called()

    def test_av03_ac01_ac03_accepts_empty_original_and_uppercase_digest(self):
        digest = hashlib.sha256(b"").hexdigest()
        envelope = self.envelope | {"original_sha256": digest.upper()}
        result = self.accept(envelope, b"")
        self.assertIs(result, self.ledger.record.return_value)
        command, _ = self.ledger.record.call_args.args
        self.assertEqual((command.original_size, command.original_sha256), (0, digest))

    def test_av10_ac01_ac03_withdrawal_uses_no_original(self):
        envelope = {
            key: value for key, value in self.envelope.items()
            if key != "original_sha256"
        } | {"operation": "WITHDRAW"}
        result = self.service.accept_revision(self.scope, envelope)
        self.assertIs(result, self.ledger.record.return_value)
        command, original_ref = self.ledger.record.call_args.args
        self.assertIsNone(command.original_size)
        self.assertIsNone(original_ref)
        self.objects.put_verified.assert_not_called()

    def test_av12_ac05_never_returns_receipt_for_unresolved_commit(self):
        self.ledger.record.side_effect = OutcomeUnknown()
        self.assertEqual(self.accept(self.envelope), Failure.OUTCOME_UNKNOWN)

    def test_av13_ac05_does_not_begin_write_when_store_fails(self):
        self.objects.put_verified.side_effect = DependencyFailure()
        self.assertEqual(self.accept(self.envelope), Failure.DEPENDENCY_FAILURE)
        self.ledger.record.assert_not_called()
