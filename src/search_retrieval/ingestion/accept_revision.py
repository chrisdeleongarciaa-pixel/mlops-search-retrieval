"""Acceptance boundary and ports for the durable revision contract v1."""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import re
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, TypeGuard
from uuid import UUID

from search_retrieval.contracts.identity import DocumentKey


class Operation(StrEnum):
    UPSERT = "UPSERT"
    WITHDRAW = "WITHDRAW"


class Failure(StrEnum):
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INTEGRITY_ERROR = "INTEGRITY_ERROR"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    STALE_REVISION = "STALE_REVISION"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class DependencyFailure(Exception):
    """A dependency failed before commit or confirmed a rollback."""


class IntegrityError(Exception):
    """The persisted original failed verification."""


class OutcomeUnknown(Exception):
    """The durable outcome of an attempted commit is still unknown."""


def _valid_identifier(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and re.search(r"[\x00\ud800-\udfff]", value) is None
    )


@dataclass(frozen=True, slots=True)
class AuthorizedSource:
    """Scope supplied by a trusted caller after authenticating the connector."""

    tenant_id: str
    source_id: str

    def __post_init__(self) -> None:
        if not _valid_identifier(self.tenant_id) or not _valid_identifier(self.source_id):
            raise ValueError("Invalid authorized source")


@dataclass(frozen=True, slots=True)
class RevisionCommand:
    """Validated semantic fields used to compare requests for one position."""

    document: DocumentKey
    source_position: int
    schema_version: int
    operation: Operation
    source_revision: str | None
    original_sha256: str | None = None
    original_size: int | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    document: DocumentKey
    source_position: int
    sequence: int
    operation: Operation
    event_id: UUID
    accepted_at: datetime


type AcceptanceResult = Receipt | Failure


class OriginalStore(Protocol):
    def put_verified(self, command: RevisionCommand, original: BinaryIO) -> str:
        """Persist and verify the original. Return an immutable reference."""
        ...


class AcceptanceLedger(Protocol):
    def resolve(self, command: RevisionCommand) -> AcceptanceResult | None:
        """Read a durable decision, or return None for a possible new acceptance."""
        ...

    def record(
        self, command: RevisionCommand, original_ref: str | None
    ) -> AcceptanceResult:
        """Recheck under serialization and commit a new acceptance atomically."""
        ...


_ENVELOPE_FIELDS = frozenset({
    "tenant_id", "source_id", "document_id", "schema_version", "source_position",
    "source_revision", "operation", "original_sha256",
})


def _parse_envelope(
    scope: AuthorizedSource, envelope: Mapping[str, object], has_original: bool
) -> RevisionCommand | Failure:
    if not isinstance(envelope, Mapping) or envelope.keys() - _ENVELOPE_FIELDS:
        return Failure.INVALID_ENVELOPE
    if (
        envelope.get("tenant_id", scope.tenant_id) != scope.tenant_id
        or envelope.get("source_id", scope.source_id) != scope.source_id
    ):
        return Failure.NOT_AUTHORIZED

    document_id = envelope.get("document_id")
    version = envelope.get("schema_version")
    position = envelope.get("source_position")
    source_revision = envelope.get("source_revision")
    if (
        not _valid_identifier(document_id)
        or type(version) is not int or version != 1
        or type(position) is not int or not 1 <= position <= 2**63 - 1
        or (source_revision is not None and not _valid_identifier(source_revision))
    ):
        return Failure.INVALID_ENVELOPE

    operation = envelope.get("operation")
    digest = envelope.get("original_sha256")
    if not isinstance(operation, str):
        return Failure.INVALID_ENVELOPE
    if operation == Operation.UPSERT:
        if not has_original or not isinstance(digest, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", digest
        ):
            return Failure.INVALID_ENVELOPE
        digest = digest.lower()
    elif operation == Operation.WITHDRAW:
        if has_original or "original_sha256" in envelope:
            return Failure.INVALID_ENVELOPE
        digest = None
    else:
        return Failure.INVALID_ENVELOPE

    return RevisionCommand(
        DocumentKey(scope.tenant_id, scope.source_id, document_id),
        position,
        version,
        Operation(operation),
        source_revision,
        digest,
    )


class RevisionAcceptance:
    """Check AC-01 to AC-05 through a transport-neutral ingestion boundary."""

    def __init__(
        self, ledger: AcceptanceLedger, objects: OriginalStore, *, max_original_bytes: int
    ) -> None:
        if type(max_original_bytes) is not int or not 0 < max_original_bytes <= 2**63 - 1:
            raise ValueError("max_original_bytes must be a positive signed 64-bit integer")
        self._ledger = ledger
        self._objects = objects
        self._limit = max_original_bytes

    def accept_revision(
        self,
        scope: AuthorizedSource,
        envelope: Mapping[str, object],
        original: BinaryIO | None = None,
    ) -> AcceptanceResult:
        """Accept a validated change for a scope authorized by the caller.

        The caller retains ownership of the input stream. A receipt identifies a
        durable acceptance. Replays return the same receipt after checking bytes.
        """
        if not isinstance(scope, AuthorizedSource):
            return Failure.NOT_AUTHORIZED
        command = _parse_envelope(scope, envelope, original is not None)
        if isinstance(command, Failure):
            return command

        try:
            if original is None:
                return self._accept(command, None)
            if not callable(getattr(original, "read", None)):
                return Failure.INVALID_ENVELOPE
            spool = SpooledTemporaryFile(max_size=min(self._limit, 1024 * 1024))
            try:
                return self._accept_original(command, original, spool)
            finally:
                # Scratch cleanup cannot change the outcome of a durable commit.
                with suppress(OSError):
                    spool.close()
        except IntegrityError:
            return Failure.INTEGRITY_ERROR
        except OutcomeUnknown:
            return Failure.OUTCOME_UNKNOWN
        except (DependencyFailure, OSError):
            return Failure.DEPENDENCY_FAILURE

    def _accept_original(
        self, command: RevisionCommand, original: BinaryIO, spool: BinaryIO
    ) -> AcceptanceResult:
        digest = sha256()
        size = 0
        while True:
            try:
                chunk = original.read(min(64 * 1024, self._limit - size + 1))
            except ValueError:
                return Failure.INVALID_ENVELOPE
            if not isinstance(chunk, bytes):
                return Failure.INVALID_ENVELOPE
            if not chunk:
                break
            size += len(chunk)
            if size > self._limit:
                return Failure.INVALID_ENVELOPE
            digest.update(chunk)
            spool.write(chunk)
        if digest.hexdigest() != command.original_sha256:
            return Failure.INTEGRITY_ERROR
        spool.seek(0)
        return self._accept(replace(command, original_size=size), spool)

    def _accept(
        self, command: RevisionCommand, original: BinaryIO | None
    ) -> AcceptanceResult:
        previous = self._ledger.resolve(command)
        if previous is not None:
            return previous
        reference = (
            self._objects.put_verified(command, original) if original is not None else None
        )
        return self._ledger.record(command, reference)
