from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from uuid import uuid4

DSN = os.environ.get("ACCEPT_REVISION_TEST_DSN")
if not DSN:
    raise unittest.SkipTest("Set ACCEPT_REVISION_TEST_DSN for the witness pipeline")

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from search_retrieval.adapters.file_objects import FileObjectStore
from search_retrieval.adapters.postgres_acceptance import PostgresAcceptanceLedger
from search_retrieval.adapters.postgres_events import PostgresEventLedger
from search_retrieval.adapters.postgres_publication import PostgresPublication
from search_retrieval.adapters.vespa import VespaWitness
from search_retrieval.query.vector import VectorSearch
from search_retrieval.ingestion.accept_revision import AuthorizedSource, Failure, Receipt, RevisionAcceptance
from search_retrieval.ingestion.publication import PublicationWorker, project
from search_retrieval.ingestion.submit_event import EventSubmission
from search_retrieval.ingestion.witness import ReplayResult, replay_witness
from search_retrieval.query.eligible import EligibleSearch, Hit

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evaluation/fixtures"
MIGRATIONS = ROOT / "infra/postgres/migrations"
SCOPE = AuthorizedSource("lab", "witness")
BODY = "El protocolo LIMA conserva el registro durante siete días.".encode()


def submit(service, event_id, position, *, doc="es-protocolo", content=BODY):
    event = dict(
        schema_version=2, source_event_id=event_id, source_position=position,
        source_revision=str(position), document_id=doc,
        operation="UPSERT" if content is not None else "WITHDRAW",
    )
    if content is None:
        return service.submit_event(SCOPE, event)
    digest = sha256(content).hexdigest()
    return service.submit_event(
        SCOPE, event | dict(original_ref=f"witness:{doc}/{position}", original_version=digest,
                            original_size=len(content), original_sha256=digest),
        BytesIO(content),
    )


def worker(mode):
    class CutLedger(PostgresEventLedger):
        def _commit(self, connection):
            if mode == "after":
                super()._commit(connection)
            Path(os.environ["WITNESS_BARRIER"]).touch()
            Event().wait()

    ledger = PostgresEventLedger(DSN) if mode == "reply" else CutLedger(DSN)
    objects = FileObjectStore(Path(os.environ["WITNESS_ORIGINALS"]))
    result = submit(EventSubmission(ledger, objects, max_original_bytes=1000), "cut", 1)
    if not isinstance(result, Receipt):
        raise RuntimeError(f"Expected a receipt, got {result}")
    print(json.dumps(asdict(result), default=str), flush=True)


class WitnessPipeline(unittest.TestCase):
    def setUp(self):
        self.schema = "witness_" + uuid4().hex
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.dsn = make_conninfo(DSN, options=f"-c search_path={self.schema} -c statement_timeout=12000")
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.objects = FileObjectStore(self.path)
        self.ledger = PostgresEventLedger(self.dsn)
        self.service = EventSubmission(self.ledger, self.objects, max_original_bytes=1000)

    def drop_schema(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def migrate(self, count=4, start=1):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            for migration in sorted(MIGRATIONS.glob("*.sql"))[start - 1:count]:
                conn.execute(migration.read_text(encoding="utf-8"))

    def query(self, statement, params=()):
        with psycopg.connect(self.dsn) as conn:
            return conn.execute(statement, params).fetchall()

    def test_se01_migration_preserves_v1_and_resolves_v2_before_document_order(self):
        self.migrate(1)
        legacy = RevisionAcceptance(PostgresAcceptanceLedger(self.dsn), self.objects, max_original_bytes=1000)
        envelope = {
            "schema_version": 1, "document_id": "es-protocolo", "source_position": 1,
            "operation": "UPSERT", "original_sha256": sha256(BODY).hexdigest(),
        }
        old = legacy.accept_revision(SCOPE, envelope, BytesIO(BODY))
        self.assertIsInstance(old, Receipt)
        self.migrate(2, start=2)
        self.assertEqual(legacy.accept_revision(SCOPE, envelope, BytesIO(BODY)), old)
        self.assertEqual(submit(self.service, "legacy-position", 1), Failure.STALE_REVISION)
        second = submit(self.service, "later", 3)
        self.assertIsInstance(second, Receipt)
        self.assertEqual(submit(self.service, "too-late", 2), Failure.STALE_REVISION)
        self.assertEqual(submit(self.service, "later", 3), second)
        self.assertEqual(submit(self.service, "later", 3, doc="different"), Failure.IDEMPOTENCY_CONFLICT)
        self.assertEqual(submit(self.service, "too-late", 2, content=b"changed"), Failure.IDEMPOTENCY_CONFLICT)
        self.assertEqual(self.query("SELECT count(*) FROM ingestion_outbox"), [(2,)])

    def test_se02_witness_resumes_contiguous_prefix_and_stops_on_conflict(self):
        self.migrate(2)
        feed, corpus = FIXTURES / "witness-feed.json", FIXTURES / "witness.json"
        self.assertEqual(replay_witness(self.service, self.ledger, feed, corpus, delivery=(1, 3)),
                         ReplayResult(1, missing_cursor=2))
        self.assertEqual(self.query("SELECT count(*) FROM ingestion_outbox"), [(1,)])
        self.assertEqual(replay_witness(self.service, PostgresEventLedger(self.dsn), feed, corpus),
                         ReplayResult(4))
        self.assertEqual(self.query("SELECT count(*) FROM ingestion_outbox"), [(4,)])
        conflicted = json.loads(feed.read_bytes())
        conflicted["events"][1]["source_event_id"] = conflicted["events"][0]["source_event_id"]
        variant = self.path / "feed.json"
        variant.write_text(json.dumps(conflicted), encoding="utf-8")
        self.assertEqual(replay_witness(self.service, self.ledger, variant, corpus),
                         ReplayResult(1, stopped=Failure.IDEMPOTENCY_CONFLICT))

    def test_se03_crash_before_and_after_commit(self):
        self.migrate(2)
        barrier = self.path / "barrier"
        env = dict(os.environ, ACCEPT_REVISION_TEST_DSN=self.dsn,
                   WITNESS_BARRIER=str(barrier), WITNESS_ORIGINALS=str(self.path))
        for mode, count in (("before", 0), ("after", 1)):
            with subprocess.Popen(
                [sys.executable, "-B", __file__, mode], env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as process:
                try:
                    deadline = monotonic() + 45
                    while not barrier.exists() and process.poll() is None and monotonic() < deadline:
                        sleep(.025)
                    self.assertTrue(barrier.exists(), process.stderr.read().decode() if process.poll() is not None else mode)
                    self.assertEqual(self.query("SELECT count(*) FROM ingestion_source_events"), [(count,)])
                finally:
                    process.kill()
                    stdout, _ = process.communicate(timeout=10)
            self.assertEqual(stdout, b"")
            barrier.unlink()
            self.assertEqual(self.query("SELECT count(*) FROM ingestion_outbox"), [(count,)])
        event, accepted, ref = self.query(
            "SELECT event_id, accepted_at, original_ref FROM ingestion_revisions"
        )[0]
        self.assertEqual((self.path / ref).read_bytes(), BODY)
        result = subprocess.run(
            [sys.executable, "-B", __file__, "reply"], env=env, capture_output=True,
            timeout=45, check=True,
        )
        receipt = json.loads(result.stdout)
        self.assertEqual((receipt["event_id"], receipt["accepted_at"]), (str(event), str(accepted)))
        self.assertEqual(self.query("SELECT count(*) FROM ingestion_outbox"), [(1,)])

    @unittest.skipUnless(os.environ.get("WITNESS_VESPA_URL"), "Set WITNESS_VESPA_URL for real Vespa")
    def test_wp01_continuity_obsolete_top_k_withdrawal_and_cached_context(self):
        self.verify_publication(VespaWitness(os.environ["WITNESS_VESPA_URL"]))

    @unittest.skipUnless(os.environ.get("WITNESS_VESPA_URL"), "Set WITNESS_VESPA_URL for real Vespa")
    def test_vector_dense_withdrawal_and_cached_context(self):
        self.verify_vector_publication("dense")

    @unittest.skipUnless(os.environ.get("WITNESS_VESPA_URL"), "Set WITNESS_VESPA_URL for real Vespa")
    def test_vector_hybrid_withdrawal_and_cached_context(self):
        self.verify_vector_publication("hybrid")

    def verify_vector_publication(self, mode):
        from types import SimpleNamespace

        class WitnessEncoder:
            space = SimpleNamespace(id="synthetic-768-unit-v1")

            def encode(self, texts, *, role):
                return [[1.0] + [0.0] * 767 for _ in texts]

        self.verify_publication(VespaWitness(os.environ["WITNESS_VESPA_URL"], encoder=WitnessEncoder()), mode)

    def test_wp02_postgres_cas_and_acl_with_controlled_index(self):
        class WitnessIndex:
            def __init__(self):
                self.rows = []

            def ensure_visible(self, work, projection, fragments):
                self.rows.extend(Hit(projection, f.fragment_id, f.text, 1) for f in fragments)

            def lexical(self, eligibility, query, k, timeout):
                return tuple(hit for hit in self.rows if hit.projection_id in eligibility.projections)[:k]

        self.verify_publication(WitnessIndex())
        ledger = PostgresPublication(self.dsn)
        public = ledger.eligibility("lab")
        self.assertEqual(len(public.projections), 1)
        doc, = self.query("SELECT id FROM ingestion_documents WHERE document_id = 'es-protocolo'")[0]
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO retrieval_private_documents VALUES (%s)", (doc,))
            conn.execute("INSERT INTO retrieval_acl VALUES (%s, 'researcher')", (doc,))
        self.assertFalse(ledger.eligibility("lab").projections)
        self.assertEqual(ledger.eligibility("lab", "researcher"), public)
        self.assertFalse(ledger.eligibility("other", "researcher").projections)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DELETE FROM retrieval_acl WHERE document_key = %s", (doc,))
        self.assertFalse(ledger.eligibility("lab", "researcher").projections)

    def verify_publication(self, index, mode=None):
        self.migrate()
        generation = "test-" + uuid4().hex
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE retrieval_generation SET active = %s", (generation,))
        ledger = PostgresPublication(self.dsn, include_fragments=bool(mode))
        vector_space = index.encoder.space.id if mode else None
        publication = PublicationWorker(ledger, self.objects, index, vector_space=vector_space)
        search = VectorSearch(ledger, index, index.encoder, generation) if mode else EligibleSearch(ledger, index)
        options = {"mode": mode} if mode else {}
        query = json.loads((FIXTURES / "witness.json").read_bytes())["queries"]["retencion-es"]["text"]

        self.assertIsInstance(submit(self.service, "first", 1), Receipt)
        self.assertEqual(publication.run_one(), "published")
        cached = search.search("lab", query, k=1, **options)
        self.assertEqual(cached.hits[0].text, BODY.decode())
        for position in range(2, 5):
            self.assertIsInstance(submit(self.service, f"update-{position}", position), Receipt)
            self.assertEqual(search.search("lab", query, k=1, **options).hits[0].text, BODY.decode())
            self.assertEqual(publication.run_one(), "published")
        current = search.search("lab", query, k=1, **options)
        self.assertEqual(len(current.hits), 1)
        self.assertNotEqual(cached.hits[0].projection_id, current.hits[0].projection_id)
        self.assertIsInstance(submit(self.service, "late", 5), Receipt)
        late = ledger.claim()
        projection, fragments, digest, manifest = project(late, BODY, vector_space=vector_space)
        self.assertIsInstance(submit(self.service, "withdraw", 6, content=None), Receipt)
        self.assertEqual(search.context("lab", current), ())
        ledger.reserve(projection, digest, manifest)
        self.assertEqual(
            self.query("SELECT manifest FROM retrieval_payloads WHERE projection_id = %s", (projection,)),
            [(manifest,)],
        )
        index.ensure_visible(late, projection, fragments)
        self.assertEqual(ledger.finish(late, "published", projection), "obsolete")
        self.assertEqual(publication.run_one(), "withdrawn")
        self.assertEqual(search.search("lab", query, **options).hits, ())
        self.assertEqual(search.context("lab", current), ())
        self.assertIsInstance(submit(self.service, "restore", 7), Receipt)
        self.assertEqual(search.search("lab", query, **options).hits, ())
        self.assertEqual(publication.run_one(), "published")
        self.assertEqual(len(search.search("lab", query, **options).hits), 1)


if __name__ == "__main__":
    worker(sys.argv[1])
