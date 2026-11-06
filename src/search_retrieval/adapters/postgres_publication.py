"""Durable outbox leases, activation CAS, and the witness search authority."""

from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from search_retrieval.ingestion.accept_revision import IntegrityError, Operation
from search_retrieval.ingestion.publication import Work
from search_retrieval.query.eligible import Eligibility, SearchUnavailable


class PostgresPublication:
    def __init__(self, dsn: str, *, lease_seconds: int = 60, max_eligible: int = 1_000):
        if lease_seconds <= 0 or max_eligible <= 0:
            raise ValueError("Lease and eligibility limits must be positive")
        self.dsn = dsn
        self.lease_seconds = lease_seconds
        self.max_eligible = max_eligible

    def claim(self) -> Work | None:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT o.event_id, g.active, d.id, d.tenant_id, r.sequence,
                       r.operation, r.original_ref, r.original_size, r.original_sha256
                FROM ingestion_outbox o
                JOIN ingestion_revisions r USING (event_id)
                JOIN ingestion_documents d ON d.id = r.document_key
                CROSS JOIN retrieval_generation g
                LEFT JOIN retrieval_attempts a USING (event_id)
                WHERE a.event_id IS NULL OR (a.outcome IS NULL AND a.expires_at <= clock_timestamp())
                ORDER BY r.accepted_at, o.event_id
                LIMIT 1 FOR UPDATE OF o SKIP LOCKED
                """
            ).fetchone()
            if row is None:
                return None
            event, generation, doc, tenant, sequence, operation, ref, size, digest = row
            token = uuid4()
            claimed = conn.execute(
                """
                INSERT INTO retrieval_attempts
                    (event_id, token, generation, expires_at, attempts)
                VALUES (%s, %s, %s, clock_timestamp() + %s * interval '1 second', 1)
                ON CONFLICT (event_id) DO UPDATE
                    SET token = EXCLUDED.token, generation = EXCLUDED.generation,
                        expires_at = EXCLUDED.expires_at, attempts = retrieval_attempts.attempts + 1
                WHERE retrieval_attempts.outcome IS NULL
                  AND retrieval_attempts.expires_at <= clock_timestamp()
                RETURNING token
                """,
                (event, token, generation, self.lease_seconds),
            ).fetchone()
            return (
                Work(event, token, generation, doc, tenant, sequence, Operation(operation), ref, size, digest)
                if claimed else None
            )

    def reserve(self, projection: str, digest: str, manifest: dict) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO retrieval_payloads VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (projection, digest, Jsonb(manifest)),
            )
            stored, = conn.execute(
                "SELECT sha256 FROM retrieval_payloads WHERE projection_id = %s", (projection,)
            ).fetchone()
            if stored != digest:
                raise IntegrityError("Projection payload changed within an immutable identity")

    def finish(self, work: Work, outcome: str, projection: str | None = None) -> str:
        with psycopg.connect(self.dsn) as conn:
            conn.execute("SET LOCAL synchronous_commit = on")
            lease = conn.execute(
                "SELECT token, outcome FROM retrieval_attempts WHERE event_id = %s FOR UPDATE",
                (work.event_id,),
            ).fetchone()
            if lease != (work.token, None):
                return "lease-lost"
            if outcome == "published":
                if not projection or work.operation != Operation.UPSERT:
                    raise ValueError("Publication requires an UPSERT projection")
                generation, = conn.execute(
                    "SELECT active FROM retrieval_generation FOR SHARE"
                ).fetchone()
                desired, = conn.execute(
                    "SELECT desired_sequence FROM ingestion_documents WHERE id = %s FOR UPDATE",
                    (work.document_key,),
                ).fetchone()
                # The desired sequence is the monotone fence. A withdrawal moves it too.
                if desired != work.sequence:
                    outcome = "obsolete"
                elif generation != work.generation:
                    conn.execute(
                        "UPDATE retrieval_attempts SET expires_at = clock_timestamp() WHERE event_id = %s",
                        (work.event_id,),
                    )
                    return "retry-generation"
                else:
                    conn.execute(
                        """
                        INSERT INTO retrieval_publications VALUES (%s, %s, %s, %s)
                        ON CONFLICT (document_key, generation) DO UPDATE
                            SET sequence = EXCLUDED.sequence, projection_id = EXCLUDED.projection_id
                        """,
                        (work.document_key, generation, work.sequence, projection),
                    )
            conn.execute(
                "UPDATE retrieval_attempts SET outcome = %s WHERE event_id = %s",
                (outcome, work.event_id),
            )
            return outcome

    def eligibility(self, tenant: str, principal: str | None = None) -> Eligibility:
        try:
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                rows = conn.execute(
                    """
                    SELECT g.active, p.projection_id
                    FROM retrieval_generation g
                    LEFT JOIN (
                        retrieval_publications p
                        JOIN ingestion_documents d ON d.id = p.document_key
                    ) ON p.generation = g.active AND d.tenant_id = %s
                      AND (
                          NOT EXISTS (
                              SELECT 1 FROM retrieval_private_documents v
                              WHERE v.document_key = d.id
                          ) OR EXISTS (
                              SELECT 1 FROM retrieval_acl a
                              WHERE a.document_key = d.id AND a.principal = %s
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM ingestion_revisions w
                          WHERE w.document_key = d.id AND w.operation = 'WITHDRAW'
                            AND w.sequence >= p.sequence AND w.sequence <= d.desired_sequence
                      )
                    ORDER BY p.projection_id
                    LIMIT %s
                    """,
                    (tenant, principal, self.max_eligible + 1),
                ).fetchall()
        except psycopg.Error as error:
            raise SearchUnavailable("Eligibility authority unavailable") from error
        if not rows or len(rows) > self.max_eligible:
            raise SearchUnavailable("Missing generation or eligibility scope exceeds laboratory limit")
        return Eligibility(tenant, rows[0][0], frozenset(row[1] for row in rows if row[1]))
