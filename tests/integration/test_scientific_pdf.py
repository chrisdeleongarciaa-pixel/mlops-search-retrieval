"""PDF-01..05: accepted original -> GROBID -> tokenizer-bounded artifacts."""

from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4


REQUIRED = ("ACCEPT_REVISION_TEST_DSN", "PDF_TEST_INPUT", "PDF_TEST_SHA256", "PDF_TEST_ENCODER_SNAPSHOT")
if not all(os.environ.get(name) for name in REQUIRED):
    raise unittest.SkipTest("Set PostgreSQL DSN, pinned PDF input/digest and encoder snapshot for PDF integration")

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from search_retrieval.adapters.file_objects import FileObjectStore
from search_retrieval.adapters.postgres_acceptance import PostgresAcceptanceLedger
from search_retrieval.ingestion.accept_revision import AuthorizedSource, Receipt, RevisionAcceptance


REPO = Path(__file__).resolve().parents[2]


class ScientificPDFIntegration(unittest.TestCase):
    def test_accepted_pdf_resolves_verified_canonical_fragments(self):
        schema = "pdf_test_" + uuid4().hex
        base_dsn = os.environ["ACCEPT_REVISION_TEST_DSN"]
        with psycopg.connect(base_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        def cleanup():
            with psycopg.connect(base_dsn, autocommit=True) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.addCleanup(cleanup)
        dsn = make_conninfo(base_dsn, options=f"-c search_path={schema}", connect_timeout=5)
        with psycopg.connect(dsn) as connection:
            connection.execute((REPO / "infra/postgres/migrations/0001_ingestion_acceptance.sql").read_text())
        if os.environ.get("PDF_TEST_OUTPUT"):
            output = Path(os.environ["PDF_TEST_OUTPUT"]).resolve()
            output.mkdir(parents=True, exist_ok=False)
        else:
            temp = TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            output = Path(temp.name)
        objects = output / "originals"
        objects.mkdir()
        payload = Path(os.environ["PDF_TEST_INPUT"]).read_bytes()
        checksum = sha256(payload).hexdigest()
        self.assertEqual(checksum, os.environ["PDF_TEST_SHA256"])
        service = RevisionAcceptance(PostgresAcceptanceLedger(dsn), FileObjectStore(objects), max_original_bytes=20*1024*1024)
        receipt = service.accept_revision(AuthorizedSource("pdf-lab", "scientific-pdf"), {
            "schema_version": 1, "document_id": "paper", "source_position": 1,
            "source_revision": checksum, "operation": "UPSERT", "original_sha256": checksum,
        }, BytesIO(payload))
        self.assertIsInstance(receipt, Receipt)
        with psycopg.connect(dsn) as connection:
            row = connection.execute("SELECT original_ref, original_size, original_sha256 FROM ingestion_revisions").fetchone()
            outbox_count = connection.execute("SELECT count(*) FROM ingestion_outbox").fetchone()[0]
        self.assertEqual(outbox_count, 1)
        self.assertEqual(FileObjectStore(objects).read_verified(*row), payload)
        receipt_file = output / "receipt.json"
        receipt_file.write_text(json.dumps({**asdict(receipt), "original_sha256": checksum}, default=str), encoding="utf-8")
        transform_python = os.environ.get("PDF_TEST_TRANSFORM_PYTHON", sys.executable)
        environment = dict(os.environ, PYTHONPATH=str(REPO / "src"))
        subprocess.run([transform_python, "-B", str(REPO / "evaluation/pdf_transform.py"),
                        str(objects / row[0]), str(receipt_file), os.environ["PDF_TEST_ENCODER_SNAPSHOT"],
                        str(output / "transformation"), "--encoder", os.environ.get("PDF_TEST_ENCODER", "e5"),
                        "--generation", "scientific-pdf-v1", "--grobid", os.environ.get("PDF_TEST_GROBID", "http://127.0.0.1:18070")],
                       env=environment, cwd=REPO, check=True, timeout=600)
        verified = subprocess.check_output([sys.executable, "-B", str(REPO / "evaluation/verify_transformation.py"),
                                            str(output / "transformation")], text=True, timeout=30)
        report = {"status": "verified", "original_sha256": checksum, "original_bytes": len(payload),
                  "receipt_sequence": receipt.sequence, "outbox_count": outbox_count,
                  "observed_original_from_independent_sql_connection": True, "transformation": json.loads(verified)}
        (output / "acceptance-verification.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
