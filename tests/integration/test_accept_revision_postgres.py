from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from random import Random
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event
import time
import unittest
from uuid import uuid4


TEST_DSN = os.environ.get("ACCEPT_REVISION_TEST_DSN")
if not TEST_DSN:
    raise unittest.SkipTest("Set ACCEPT_REVISION_TEST_DSN to a disposable PostgreSQL database")

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from search_retrieval.adapters.file_objects import FileObjectStore
from search_retrieval.adapters.postgres_acceptance import PostgresAcceptanceLedger
from search_retrieval.ingestion.accept_revision import (
    AuthorizedSource,
    Failure,
    Receipt,
    RevisionAcceptance,
)


_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra/postgres/migrations/0001_ingestion_acceptance.sql"
)
_SCOPE = AuthorizedSource("tenant", "source")
_ORIGINAL = b"durable acceptance fixture"


def _submit(service, position=7, content=_ORIGINAL, *, doc="doc", label=None, scope=_SCOPE):
    envelope = {
        "schema_version": 1,
        "document_id": doc,
        "source_position": position,
        "source_revision": label,
        "operation": "UPSERT" if content is not None else "WITHDRAW",
    }
    if content is None:
        return service.accept_revision(scope, envelope)
    envelope["original_sha256"] = sha256(content).hexdigest()
    return service.accept_revision(scope, envelope, io.BytesIO(content))


def _receipt_fields(receipt):
    return json.loads(json.dumps(asdict(receipt), default=str))


def _worker(mode):
    root = Path(os.environ["ACCEPT_REVISION_TEST_OBJECTS"])
    barrier = Path(os.environ["ACCEPT_REVISION_TEST_BARRIER"])

    def pause():
        barrier.touch(exist_ok=False)
        Event().wait()

    class PausingObjects(FileObjectStore):
        def put_verified(self, command, original):
            reference = super().put_verified(command, original)
            pause()
            return reference

    objects = PausingObjects(root) if mode == "after-object" else FileObjectStore(root)
    service = RevisionAcceptance(
        PostgresAcceptanceLedger(TEST_DSN), objects, max_original_bytes=1024
    )
    receipt = _submit(service)
    if not isinstance(receipt, Receipt):
        raise RuntimeError(f"Acceptance failed: {receipt}")
    if mode == "after-commit":
        pause()
    print(json.dumps(_receipt_fields(receipt)), flush=True)


class PostgresAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.schema = "accept_test_" + uuid4().hex
        with psycopg.connect(TEST_DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._drop_schema)
        self.dsn = make_conninfo(
            TEST_DSN,
            options=f"-c search_path={self.schema} -c lock_timeout=6000 -c statement_timeout=12000",
            connect_timeout=5,
        )
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(_MIGRATION.read_text(encoding="utf-8"))
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.objects_path = self.root / "objects"
        self.objects_path.mkdir()
        self.service = self._service()

    def _drop_schema(self):
        with psycopg.connect(TEST_DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def _service(self, ledger=None):
        return RevisionAcceptance(
            ledger or PostgresAcceptanceLedger(self.dsn),
            FileObjectStore(self.objects_path),
            max_original_bytes=1024,
        )

    def _state(self, doc="doc", scope=_SCOPE):
        with psycopg.connect(self.dsn) as conn:
            return conn.execute(
                """
                SELECT d.desired_sequence, r.sequence, r.source_position, r.operation,
                       r.original_ref, r.event_id, r.accepted_at, o.event_id
                FROM ingestion_documents d
                JOIN ingestion_revisions r ON r.document_key = d.id
                LEFT JOIN ingestion_outbox o ON o.event_id = r.event_id
                WHERE (d.tenant_id, d.source_id, d.document_id) = (%s, %s, %s)
                ORDER BY r.sequence
                """,
                (scope.tenant_id, scope.source_id, doc),
            ).fetchall()

    def _execute(self, statement):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(statement)

    def _worker_env(self):
        return dict(
            os.environ,
            ACCEPT_REVISION_TEST_DSN=self.dsn,
            ACCEPT_REVISION_TEST_OBJECTS=str(self.objects_path),
            ACCEPT_REVISION_TEST_BARRIER=str(self.root / "barrier"),
        )

    def _kill_at_barrier(self, mode, inspect):
        env = self._worker_env()
        command = [sys.executable, "-B", __file__, mode]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            try:
                deadline = time.monotonic() + 20
                while not Path(env["ACCEPT_REVISION_TEST_BARRIER"]).exists():
                    if proc.poll() is not None:
                        _, stderr = proc.communicate()
                        self.fail(stderr.decode(errors="replace"))
                    if time.monotonic() >= deadline:
                        self.fail(f"Child did not reach the {mode} barrier")
                    time.sleep(0.025)
                inspect()
            finally:
                proc.kill()
                stdout, _ = proc.communicate(timeout=10)
        self.assertEqual(stdout, b"")
        self.assertNotEqual(proc.returncode, 0)

    def test_av02_ac03_ac05_detects_corrupt_persisted_object(self):
        first = _submit(self.service)
        state = self._state()
        original = self.objects_path / state[0][4]
        original.write_bytes(b"x" * len(_ORIGINAL))
        self.assertEqual(_submit(self.service, 8), Failure.INTEGRITY_ERROR)
        self.assertEqual(self._state(), state)
        # A historical receipt can be resolved without accessing that object.
        self.assertEqual(_submit(self.service), first)

    def test_av03_ac03_ac04_commits_verified_object_revision_and_event(self):
        receipt = _submit(self.service)
        self.assertIsInstance(receipt, Receipt)
        row, = self._state()
        self.assertEqual(row[:4], (1, 1, 7, "UPSERT"))
        self.assertEqual((self.objects_path / row[4]).read_bytes(), _ORIGINAL)
        self.assertEqual(row[5:], (receipt.event_id, receipt.accepted_at, receipt.event_id))

    def test_av04_ac02_ac03_ac04_ac05_restart_after_commit_before_receipt(self):
        observed = []

        def capture():
            row, = self._state()
            self.assertEqual(row[:4], (1, 1, 7, "UPSERT"))
            self.assertEqual(row[5], row[7])
            observed.append(row)

        self._kill_at_barrier("after-commit", capture)
        self.assertEqual(self._state(), observed)
        row, = observed
        self.assertEqual(sha256((self.objects_path / row[4]).read_bytes()).digest(), sha256(_ORIGINAL).digest())
        completed = subprocess.run(
            [sys.executable, "-B", __file__, "reply"], env=self._worker_env(),
            capture_output=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(completed.stdout), {
            "document": {"tenant_id": "tenant", "source_id": "source", "document_id": "doc"},
            "source_position": 7,
            "sequence": 1,
            "operation": "UPSERT",
            "event_id": str(row[5]),
            "accepted_at": str(row[6]),
        })
        self.assertEqual(self._state(), observed)

    def test_av05_ac03_ac05_outbox_failure_rolls_back_revision_and_desired_pointer(self):
        self.assertIsInstance(_submit(self.service, 6), Receipt)
        before = self._state()
        self._execute("ALTER TABLE ingestion_outbox ADD CONSTRAINT reject_event CHECK (false) NOT VALID")
        self.assertEqual(_submit(self.service, 7, b"new bytes"), Failure.DEPENDENCY_FAILURE)
        self.assertEqual(self._state(), before)
        references = {row[4] for row in before}
        orphan, = [obj for obj in self.objects_path.iterdir() if obj.name not in references]
        self.assertEqual(orphan.read_bytes(), b"new bytes")
        self._execute("ALTER TABLE ingestion_outbox DROP CONSTRAINT reject_event")
        result = _submit(self.service, 7, b"new bytes")
        self.assertIsInstance(result, Receipt)
        self.assertEqual(len(self._state()), 2)
        self.assertEqual(_submit(self.service, 7, b"new bytes"), result)

    def test_av06_av07_ac02_ac04_ac05_distinguishes_replay_conflict_and_stale(self):
        seven = _submit(self.service, 7, label="Z")
        nine = _submit(self.service, 9, label="A")
        self.assertIsInstance(seven, Receipt)
        self.assertIsInstance(nine, Receipt)
        before = self._state()
        self.assertEqual(_submit(self.service, 7, label="Z"), seven)
        self.assertEqual(_submit(self.service, 8), Failure.STALE_REVISION)
        for content, label in ((b"other", "Z"), (None, "Z"), (_ORIGINAL, "other")):
            self.assertEqual(_submit(self.service, 7, content, label=label), Failure.IDEMPOTENCY_CONFLICT)
        self.assertEqual(self._state(), before)
        self.assertEqual([(r[0], r[1], r[2]) for r in before], [(2, 1, 7), (2, 2, 9)])

    def test_av08_ac02_ac04_serializes_concurrent_first_writes(self):
        for doc, other in (("same", _ORIGINAL), ("conflict", b"competing bytes")):
            with self.subTest(doc=doc):
                gate = Barrier(2, timeout=10)

                class RacingLedger(PostgresAcceptanceLedger):
                    def record(self, command, original_ref):
                        gate.wait()
                        return super().record(command, original_ref)

                service = self._service(RacingLedger(self.dsn))
                with ThreadPoolExecutor(2) as pool:
                    left = pool.submit(_submit, service, doc=doc)
                    right = pool.submit(_submit, service, 7, other, doc=doc)
                    results = [left.result(timeout=20), right.result(timeout=20)]
                row, = self._state(doc)
                self.assertEqual(row[5], row[7])
                receipts = [result for result in results if isinstance(result, Receipt)]
                self.assertTrue(receipts)
                self.assertTrue(all(result.event_id == row[5] for result in receipts))
                if other == _ORIGINAL:
                    self.assertEqual(results, [receipts[0], receipts[0]])
                else:
                    self.assertEqual(len(receipts), 1)
                    self.assertIn(Failure.IDEMPOTENCY_CONFLICT, results)

    def test_av09_ac04_covers_both_orders_for_overlapping_positions(self):
        for fast, slow in ((7, 8), (8, 7)):
            doc = f"first-{fast}"
            ready, proceed = Event(), Event()

            class OrderedLedger(PostgresAcceptanceLedger):
                def record(self, command, original_ref):
                    if command.source_position == slow:
                        ready.set()
                        if not proceed.wait(10):
                            raise TimeoutError("Concurrent history did not release the writer")
                    return super().record(command, original_ref)

            service = self._service(OrderedLedger(self.dsn))
            with ThreadPoolExecutor(1) as pool:
                later = pool.submit(_submit, service, slow, doc=doc)
                try:
                    self.assertTrue(ready.wait(10))
                    self.assertIsInstance(_submit(service, fast, doc=doc), Receipt)
                finally:
                    proceed.set()
                result = later.result(timeout=20)
            rows = self._state(doc)
            if fast == 7:
                self.assertIsInstance(result, Receipt)
                self.assertEqual([(r[0], r[2]) for r in rows], [(2, 7), (2, 8)])
            else:
                self.assertEqual(result, Failure.STALE_REVISION)
                self.assertEqual([(r[0], r[2]) for r in rows], [(1, 8)])
            self.assertTrue(all(row[5] == row[7] for row in rows))

    def test_av10_ac03_ac04_historical_upsert_cannot_replace_desired_withdrawal(self):
        seven = _submit(self.service)
        eight = _submit(self.service, 8, None)
        self.assertIsInstance(seven, Receipt)
        self.assertIsInstance(eight, Receipt)
        self.assertEqual(_submit(self.service), seven)
        upsert, withdrawal = self._state()
        self.assertEqual(upsert[0], 2)
        self.assertEqual(withdrawal[:5], (2, 2, 8, "WITHDRAW", None))
        self.assertEqual(withdrawal[7], eight.event_id)

    def test_av11_ac01_ac02_ac04_keeps_exact_long_identities_in_separate_scopes(self):
        doc = "document-" + Random(11).randbytes(4096).hex()
        scopes = (_SCOPE, AuthorizedSource("tenant", "second"), AuthorizedSource("another", "source"))
        receipts = [_submit(self.service, doc=doc, scope=scope) for scope in scopes]
        self.assertTrue(all(isinstance(receipt, Receipt) for receipt in receipts))
        self.assertEqual(len({receipt.event_id for receipt in receipts}), 3)
        for scope, receipt in zip(scopes, receipts):
            row, = self._state(doc, scope)
            self.assertEqual((row[0], row[1], row[5]), (1, 1, receipt.event_id))

    def test_av12_ac05_recovers_unknown_commit_and_unknown_rollback(self):
        for committed in (True, False):
            doc = f"commit-{committed}"

            class LostConfirmation(psycopg.Connection):
                def commit(self):
                    if committed:
                        super().commit()
                    else:
                        super().rollback()
                    self.close()
                    raise psycopg.OperationalError("Injected lost confirmation")

            service = self._service(PostgresAcceptanceLedger(self.dsn, connect=LostConfirmation.connect))
            self.assertEqual(_submit(service, doc=doc), Failure.OUTCOME_UNKNOWN)
            before = self._state(doc)
            self.assertEqual(len(before), int(committed))
            result = _submit(self.service, doc=doc)
            self.assertIsInstance(result, Receipt)
            row, = self._state(doc)
            self.assertEqual((row[5], row[7]), (result.event_id, result.event_id))
            if committed:
                self.assertEqual([row], before)

    def test_av13_ac03_ac05_object_survives_cut_before_acceptance(self):
        self.objects_path.rmdir()
        self.assertEqual(_submit(self.service), Failure.DEPENDENCY_FAILURE)
        self.assertEqual(self._state(), [])
        self.objects_path.mkdir()
        self._kill_at_barrier("after-object", lambda: self.assertEqual(self._state(), []))
        self.assertEqual(self._state(), [])
        original, = self.objects_path.iterdir()
        self.assertEqual(original.read_bytes(), _ORIGINAL)
        self.assertIsInstance(_submit(self.service), Receipt)
        self.assertEqual(len(self._state()), 1)

    def test_ac02_ac04_generated_histories_match_independent_state(self):
        random = Random(217)
        accepted = {}
        for _ in range(45):
            position = random.randint(1, 12)
            body = random.choice((None, b"a", b"b"))
            result = _submit(self.service, position, body)
            if position in accepted:
                previous_body, previous_receipt = accepted[position]
                expected = previous_receipt if body == previous_body else Failure.IDEMPOTENCY_CONFLICT
                self.assertEqual(result, expected)
            elif accepted and position < max(accepted):
                self.assertEqual(result, Failure.STALE_REVISION)
            else:
                self.assertIsInstance(result, Receipt)
                self.assertEqual(result.sequence, len(accepted) + 1)
                accepted[position] = (body, result)
            rows = self._state()
            self.assertEqual([r[2] for r in rows], sorted(accepted))
            self.assertTrue(all(r[0] == len(accepted) and r[5] == r[7] for r in rows))


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in {"after-commit", "after-object", "reply"}:
        _worker(sys.argv[1])
    else:
        unittest.main()
