"""Versioned ingestion envelope. Cursor belongs to the connector."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from search_retrieval.ingestion.accept_revision import (
    AcceptanceResult,
    AuthorizedSource,
    Failure,
    Operation,
    OriginalStore,
    RevisionAcceptance,
    RevisionCommand,
    _valid_identifier,
)


@dataclass(frozen=True, slots=True)
class SourceEvent:
    event_id: str
    original_ref: str | None
    original_version: str | None
    original_size: int | None


class SourceEventLedger(Protocol):
    def resolve_event(
        self, event: SourceEvent, command: RevisionCommand
    ) -> AcceptanceResult | None: ...

    def record_event(
        self, event: SourceEvent, command: RevisionCommand, reference: str | None
    ) -> AcceptanceResult: ...


@dataclass(frozen=True, slots=True)
class _BoundEvent:
    ledger: SourceEventLedger
    event: SourceEvent

    def resolve(self, command: RevisionCommand) -> AcceptanceResult | None:
        if self.event.original_size != command.original_size:
            return Failure.INTEGRITY_ERROR
        return self.ledger.resolve_event(self.event, command)

    def record(self, command: RevisionCommand, original_ref: str | None) -> AcceptanceResult:
        return self.ledger.record_event(self.event, command, original_ref)


class EventSubmission:
    """Accept a v2 event from an authenticated scope and a trusted byte reader."""

    def __init__(
        self, ledger: SourceEventLedger, objects: OriginalStore, *, max_original_bytes: int
    ) -> None:
        if type(max_original_bytes) is not int or not 0 < max_original_bytes < 2**63:
            raise ValueError("max_original_bytes must be a positive signed 64-bit integer")
        self._ledger = ledger
        self._objects = objects
        self._limit = max_original_bytes

    def submit_event(
        self,
        scope: AuthorizedSource,
        envelope: Mapping[str, object],
        original: BinaryIO | None = None,
    ) -> AcceptanceResult:
        """Verify source identity and bytes before a durable event decision.

        The caller opens the original inside its authorized source. References
        in the envelope are provenance and are never fetched by this method.
        """
        if not isinstance(scope, AuthorizedSource):
            return Failure.NOT_AUTHORIZED
        if not isinstance(envelope, Mapping):
            return Failure.INVALID_ENVELOPE
        version = envelope.get("schema_version")
        event_id = envelope.get("source_event_id")
        if (
            type(version) is not int or version != 2
            or not _valid_identifier(event_id)
            or not _valid_identifier(envelope.get("source_revision"))
        ):
            return Failure.INVALID_ENVELOPE
        fields = dict(envelope)
        del fields["source_event_id"]
        reference = fields.pop("original_ref", None)
        object_version = fields.pop("original_version", None)
        size = fields.pop("original_size", None)
        if fields.get("operation") == Operation.UPSERT:
            if (
                not _valid_identifier(reference) or not _valid_identifier(object_version)
                or type(size) is not int or not 0 <= size <= self._limit
            ):
                return Failure.INVALID_ENVELOPE
        elif {"original_ref", "original_version", "original_size"} & envelope.keys():
            return Failure.INVALID_ENVELOPE
        fields["schema_version"] = 1
        event = SourceEvent(event_id, reference, object_version, size)
        acceptance = RevisionAcceptance(
            _BoundEvent(self._ledger, event), self._objects, max_original_bytes=self._limit
        )
        return acceptance.accept_revision(scope, fields, original)
