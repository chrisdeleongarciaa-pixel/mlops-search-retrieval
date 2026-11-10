"""PostgreSQL acceptance ledger for AC-02 through AC-05."""

from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

import psycopg

from search_retrieval.contracts.identity import DocumentKey
from search_retrieval.ingestion.accept_revision import (
    AcceptanceResult,
    DependencyFailure,
    Failure,
    Operation,
    OutcomeUnknown,
    Receipt,
    RevisionCommand,
)


_STATE_SQL = """
    SELECT d.id, d.desired_sequence, desired.source_position,
           prior.sequence, prior.schema_version, prior.operation,
           prior.source_revision, prior.original_sha256, prior.original_size,
           prior.event_id, prior.accepted_at
    FROM ingestion_documents AS d
    LEFT JOIN ingestion_revisions AS desired
        ON (desired.document_key, desired.sequence) = (d.id, d.desired_sequence)
    LEFT JOIN ingestion_revisions AS prior
        ON prior.document_key = d.id AND prior.source_position = %s
    WHERE d.lock_key = %s
      AND (d.tenant_id, d.source_id, d.document_id) = (%s, %s, %s)
"""


def _lock_key(document: DocumentKey) -> int:
    parts = (document.tenant_id, document.source_id, document.document_id)
    digest = sha256(json.dumps(parts).encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PostgresAcceptanceLedger:
    """Serialize writers per document and resolve historical replays.

    Connections are short-lived and unshared. Every writer of acceptance
    tables must follow the same transaction lock protocol.
    """

    def __init__(self, dsn: str, *, connect=psycopg.connect) -> None:
        self._dsn = dsn
        self._connect = connect

    def resolve(self, command: RevisionCommand) -> AcceptanceResult | None:
        try:
            with closing(self._connect(self._dsn, autocommit=True)) as connection:
                result, _, _ = self._decision(connection, command, _lock_key(command.document))
                return result
        except psycopg.Error as error:
            raise DependencyFailure("Acceptance ledger read failed") from error

    def record(
        self, command: RevisionCommand, original_ref: str | None
    ) -> AcceptanceResult:
        try:
            with closing(self._connect(self._dsn, autocommit=False)) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                connection.execute("SET LOCAL synchronous_commit = on")
                lock_key = _lock_key(command.document)
                connection.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (lock_key,))
                result, document_key, sequence = self._decision(connection, command, lock_key)
                if result is not None:
                    return result
                if document_key is None:
                    document_key = self._insert_document(connection, command.document, lock_key)
                receipt = self._insert_acceptance(
                    connection, command, original_ref, document_key, sequence + 1
                )
                self._commit(connection)
                return receipt
        except psycopg.Error as error:
            raise DependencyFailure("Acceptance ledger write failed before commit") from error

    @staticmethod
    def _decision(connection, command: RevisionCommand, lock_key: int):
        document = command.document
        row = connection.execute(
            _STATE_SQL,
            (
                command.source_position, lock_key,
                document.tenant_id, document.source_id, document.document_id,
            ),
        ).fetchone()
        if row is None:
            return None, None, 0
        (
            document_key, sequence, source_position, prior_sequence, version,
            operation, source_revision, digest, size, event_id, accepted_at,
        ) = row
        if prior_sequence is not None:
            previous = replace(
                command,
                schema_version=version,
                operation=Operation(operation),
                source_revision=source_revision,
                original_sha256=digest,
                original_size=size,
            )
            result = (
                Receipt(
                    document, command.source_position, prior_sequence,
                    previous.operation, event_id, accepted_at,
                )
                if previous == command else Failure.IDEMPOTENCY_CONFLICT
            )
        elif source_position is not None and command.source_position <= source_position:
            result = Failure.STALE_REVISION
        else:
            result = None
        return result, document_key, sequence or 0

    @staticmethod
    def _insert_document(connection, document: DocumentKey, lock_key: int) -> int:
        key, = connection.execute(
            """
            INSERT INTO ingestion_documents (lock_key, tenant_id, source_id, document_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (lock_key, document.tenant_id, document.source_id, document.document_id),
        ).fetchone()
        return key

    @staticmethod
    def _insert_acceptance(
        connection, command: RevisionCommand, original_ref: str | None,
        document_key: int, sequence: int,
    ) -> Receipt:
        event_id = uuid4()
        accepted_at, = connection.execute(
            """
            INSERT INTO ingestion_revisions (
                document_key, sequence, source_position, schema_version, operation,
                source_revision, original_size, original_sha256, original_ref, event_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING accepted_at
            """,
            (
                document_key, sequence, command.source_position, command.schema_version,
                command.operation.value, command.source_revision, command.original_size,
                command.original_sha256, original_ref, event_id,
            ),
        ).fetchone()
        connection.execute("INSERT INTO ingestion_outbox (event_id) VALUES (%s)", (event_id,))
        connection.execute(
            "UPDATE ingestion_documents SET desired_sequence = %s WHERE id = %s",
            (sequence, document_key),
        )
        return Receipt(
            command.document, command.source_position, sequence,
            command.operation, event_id, accepted_at,
        )

    @staticmethod
    def _commit(connection) -> None:
        try:
            connection.commit()
        except psycopg.Error as error:
            state = error.sqlstate or ""
            if state.startswith("23") or state in {"40001", "40P01"}:
                raise DependencyFailure("PostgreSQL rejected acceptance commit") from error
            raise OutcomeUnknown("PostgreSQL did not confirm acceptance commit") from error
