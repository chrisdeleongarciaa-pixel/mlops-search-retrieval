"""Durable source event decisions and witness cursor persistence."""

from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json

import psycopg
from psycopg.types.json import Jsonb

from search_retrieval.adapters.postgres_acceptance import PostgresAcceptanceLedger, _lock_key
from search_retrieval.ingestion.accept_revision import (
    AcceptanceResult, DependencyFailure, Failure, Operation, Receipt, RevisionCommand,
)
from search_retrieval.ingestion.submit_event import SourceEvent


class PostgresEventLedger(PostgresAcceptanceLedger):
    """Serialize event identity first, then document order, in one transaction."""

    def resolve_event(
        self, event: SourceEvent, command: RevisionCommand
    ) -> AcceptanceResult | None:
        try:
            with closing(self._connect(self._dsn, autocommit=True)) as connection:
                return self._lookup_event(connection, event, command)
        except psycopg.Error as error:
            raise DependencyFailure("Source event read failed") from error

    def record_event(
        self, event: SourceEvent, command: RevisionCommand, reference: str | None
    ) -> AcceptanceResult:
        try:
            with closing(self._connect(self._dsn, autocommit=False)) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                connection.execute("SET LOCAL synchronous_commit = on")
                key, tenant, source, event_id = self._event_key(event, command)
                # The two-integer lock space is distinct from document bigint locks.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s::integer, %s::integer)",
                    tuple(int.from_bytes(key[i:i + 4], "big", signed=True) for i in (0, 4)),
                )
                existing = self._lookup_event(connection, event, command)
                if existing is not None:
                    return existing

                lock_key = _lock_key(command.document)
                connection.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (lock_key,))
                decision, document_key, sequence = self._decision(connection, command, lock_key)
                result = Failure.STALE_REVISION
                if decision is None:
                    if document_key is None:
                        document_key = self._insert_document(connection, command.document, lock_key)
                    result = self._insert_acceptance(
                        connection, command, reference, document_key, sequence + 1
                    )
                connection.execute(
                    """
                    INSERT INTO ingestion_source_events
                        (lookup_key, tenant_id, source_id, source_event_id, semantic, receipt_event_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        key, tenant, source, event_id, Jsonb(self._semantic(event, command)),
                        result.event_id if isinstance(result, Receipt) else None,
                    ),
                )
                self._commit(connection)
                return result
        except psycopg.Error as error:
            raise DependencyFailure("Source event write failed") from error

    @staticmethod
    def _event_key(event: SourceEvent, command: RevisionCommand) -> tuple[bytes, str, str, str]:
        scope = command.document.tenant_id, command.document.source_id, event.event_id
        return sha256(json.dumps(scope).encode("ascii")).digest(), *scope

    @staticmethod
    def _semantic(event: SourceEvent, command: RevisionCommand) -> dict:
        return {
            **asdict(command), "schema_version": 2, "original_ref": event.original_ref,
            "original_version": event.original_version,
        }

    def _lookup_event(
        self, connection, event: SourceEvent, command: RevisionCommand
    ) -> AcceptanceResult | None:
        row = connection.execute(
            """
            SELECT e.semantic, r.sequence, r.source_position, r.operation, r.event_id, r.accepted_at
            FROM ingestion_source_events AS e
            LEFT JOIN ingestion_revisions AS r ON r.event_id = e.receipt_event_id
            WHERE (e.lookup_key, e.tenant_id, e.source_id, e.source_event_id) = (%s, %s, %s, %s)
            """,
            self._event_key(event, command),
        ).fetchone()
        if row is None:
            return None
        semantic, sequence, position, operation, event_id, accepted_at = row
        if semantic != self._semantic(event, command):
            return Failure.IDEMPOTENCY_CONFLICT
        if event_id is None:
            return Failure.STALE_REVISION
        return Receipt(command.document, position, sequence, Operation(operation), event_id, accepted_at)

    def checkpoint(self, digest: str) -> int:
        with closing(self._connect(self._dsn, autocommit=True)) as connection:
            row = connection.execute(
                "SELECT cursor FROM ingestion_witness_checkpoints WHERE feed_sha256 = %s", (digest,)
            ).fetchone()
            return row[0] if row else 0

    def advance_checkpoint(self, digest: str, cursor: int) -> None:
        """Advance one resolved cursor. Repeating an already saved cursor is safe."""
        with self._connect(self._dsn) as connection:
            connection.execute("SET LOCAL synchronous_commit = on")
            connection.execute(
                """INSERT INTO ingestion_witness_checkpoints (feed_sha256, cursor)
                   VALUES (%s, 0) ON CONFLICT DO NOTHING""", (digest,),
            )
            saved, = connection.execute(
                "SELECT cursor FROM ingestion_witness_checkpoints WHERE feed_sha256 = %s FOR UPDATE",
                (digest,),
            ).fetchone()
            if cursor <= saved:
                return
            if cursor != saved + 1:
                raise ValueError("Checkpoint would skip an unresolved cursor")
            connection.execute(
                "UPDATE ingestion_witness_checkpoints SET cursor = %s WHERE feed_sha256 = %s",
                (cursor, digest),
            )
