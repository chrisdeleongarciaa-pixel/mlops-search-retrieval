"""ACL authority over the existing PostgreSQL tables.

A document is private when `retrieval_private_documents` names it; a principal may read it when
`retrieval_acl` grants it. Documents are found by (tenant, source, document ID) in
`ingestion_documents`, with the lock key of the acceptance protocol. Public documents are not
recorded. Every failure of the authority raises SearchUnavailable: the service fails closed.
"""

from datetime import datetime
import threading

import psycopg

from search_retrieval.adapters.postgres_acceptance import _lock_key
from search_retrieval.contracts.identity import DocumentKey
from search_retrieval.query.eligible import SearchUnavailable


PUBLIC = "public"

DENIED = """
SELECT d.document_id
FROM ingestion_documents d
JOIN retrieval_private_documents p ON p.document_key = d.id
WHERE d.tenant_id = %s AND d.source_id = %s AND d.document_id = ANY(%s)
  AND NOT EXISTS (SELECT 1 FROM retrieval_acl a WHERE a.document_key = d.id AND a.principal = %s)
"""


class PostgresAcl:
    def __init__(self, dsn: str, *, tenant: str, source: str):
        if not tenant or not source:
            raise ValueError("The authority needs a tenant and a source")
        self.dsn, self.tenant, self.source = dsn, tenant, source
        self._local = threading.local()

    def _connection(self) -> psycopg.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None or connection.closed:
            connection = self._local.connection = psycopg.connect(self.dsn, autocommit=True)
        return connection

    def tokens(self, principal: str | None) -> list[str]:
        """Engine filter: public documents, and private ones granted to the principal."""
        return [PUBLIC] if principal is None else [PUBLIC, principal]

    def denied(self, principal: str | None, doc_ids: list[str]) -> set[str]:
        """The documents among `doc_ids` that are private and not granted to the principal."""
        if not doc_ids:
            return set()
        try:
            rows = self._connection().execute(DENIED, (self.tenant, self.source, doc_ids, principal or "")).fetchall()
        except psycopg.Error as error:
            self._local.connection = None
            raise SearchUnavailable("ACL authority unavailable") from error
        return {row[0] for row in rows}

    # Administration, used by the revocation measurement.

    def make_private(self, grants: dict[str, str]) -> None:
        """Make each document private and grant it to one principal."""
        with psycopg.connect(self.dsn) as connection:
            for doc_id, principal in grants.items():
                key = _lock_key(DocumentKey(self.tenant, self.source, doc_id))
                row = connection.execute(
                    "SELECT id FROM ingestion_documents WHERE lock_key = %s AND (tenant_id, source_id, document_id) = (%s, %s, %s)",
                    (key, self.tenant, self.source, doc_id)).fetchone()
                document = row[0] if row else connection.execute(
                    "INSERT INTO ingestion_documents (lock_key, tenant_id, source_id, document_id) VALUES (%s, %s, %s, %s) "
                    "RETURNING id", (key, self.tenant, self.source, doc_id)).fetchone()[0]
                connection.execute("INSERT INTO retrieval_private_documents VALUES (%s) ON CONFLICT DO NOTHING", (document,))
                connection.execute("INSERT INTO retrieval_acl VALUES (%s, %s) ON CONFLICT DO NOTHING", (document, principal))

    def revoke(self, doc_id: str, principal: str) -> datetime:
        """Delete one grant and return the commit time reported by the server."""
        with psycopg.connect(self.dsn) as connection:
            removed = connection.execute(
                "DELETE FROM retrieval_acl a USING ingestion_documents d WHERE a.document_key = d.id "
                "AND (d.tenant_id, d.source_id, d.document_id) = (%s, %s, %s) AND a.principal = %s",
                (self.tenant, self.source, doc_id, principal)).rowcount
            if removed != 1:
                raise ValueError(f"No grant of {doc_id} to {principal}")
            committed = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        return committed

    def make_public(self, doc_ids: list[str]) -> None:
        """Remove the grants and the private mark of these documents."""
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "DELETE FROM retrieval_acl a USING ingestion_documents d WHERE a.document_key = d.id "
                "AND d.tenant_id = %s AND d.source_id = %s AND d.document_id = ANY(%s)", (self.tenant, self.source, doc_ids))
            connection.execute(
                "DELETE FROM retrieval_private_documents p USING ingestion_documents d WHERE p.document_key = d.id "
                "AND d.tenant_id = %s AND d.source_id = %s AND d.document_id = ANY(%s)", (self.tenant, self.source, doc_ids))
