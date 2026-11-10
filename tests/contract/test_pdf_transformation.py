"""PDF-01..04 with hand-calculated Unicode offsets and adversarial TEI."""

from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from search_retrieval.adapters.grobid import Grobid, verify_pdf
from search_retrieval.contracts.identity import DocumentKey, RevisionKey
from search_retrieval.ingestion.transformation import Recipe, TransformationError, transform_revision


TEI = b'''<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div>
<head coords="1,10,20,100,12">Methods</head>
<p coords="1,10,40,100,25;2,10,20,100,25">A caf&#233; model. Evidence on two pages.</p>
<figure><head>Excluded caption</head><p>Excluded figure</p></figure>
</div></body><back><p>Excluded reference</p></back></text></TEI>'''


class PDFContract(unittest.TestCase):
    def setUp(self):
        self.revision = RevisionKey(DocumentKey("tenant", "source", "paper"), 1)
        self.recipe = Recipe("grobid-pinned", "test-word-counter-v1", "generation-a", max_tokens=6)

    def transform(self, tei=TEI, recipe=None):
        return transform_revision(self.revision, "a" * 64, tei, recipe or self.recipe,
                                  lambda text: len(text.split()) + 2)

    def test_pdf03_exact_unicode_spans_and_multipage_locator(self):
        text, manifest = self.transform()
        self.assertEqual(text, "Methods\n\nA café model. Evidence on two pages.".encode())
        self.assertEqual(len(manifest["fragments"]), 3)
        for fragment in manifest["fragments"]:
            payload = text[fragment["start"]:fragment["end"]]
            self.assertEqual(sha256(payload).hexdigest(), fragment["sha256"])
            self.assertLessEqual(fragment["tokens"], 6)
        self.assertEqual(manifest["fragments"][1]["sections"], ["Methods"])
        self.assertEqual([b["page"] for b in manifest["fragments"][1]["boxes"]], [1, 2])
        self.assertEqual(manifest["fragments"][2]["boxes"], manifest["fragments"][1]["boxes"])

    def test_pdf04_replay_and_recipe_generation_changes(self):
        first = self.transform()
        self.assertEqual(first, self.transform())
        for recipe in (replace(self.recipe, generation="b"), replace(self.recipe, max_tokens=9)):
            self.assertNotEqual(first[1]["transformation_id"], self.transform(recipe=recipe)[1]["transformation_id"])
        changed = TEI.replace(b"1,10,20,100,12", b"1,12,20,100,12")
        self.assertNotEqual(first[1]["transformation_id"], self.transform(changed)[1]["transformation_id"])

    def test_pdf02_partial_external_entities_and_bad_coordinates_rejected(self):
        for tei in (TEI[:-12], b'<!DOCTYPE TEI [<!ENTITY e SYSTEM "file:///private">]>' + TEI,
                    TEI.replace(b"1,10,20,100,12", b"1,nan,20,100,12"), b"<html/>"):
            with self.assertRaises(TransformationError):
                self.transform(tei)

    def test_pdf03_limits_and_missing_locator(self):
        with self.assertRaisesRegex(TransformationError, "OUTPUT_LIMIT"):
            self.transform(recipe=replace(self.recipe, max_tei_bytes=5))
        with self.assertRaisesRegex(TransformationError, "BLOCK_LIMIT"):
            self.transform(recipe=replace(self.recipe, max_blocks=1))
        with self.assertRaisesRegex(TransformationError, "UNSPLITTABLE"):
            self.transform(recipe=replace(self.recipe, max_tokens=1))
        _, manifest = self.transform(TEI.replace(b' coords="1,10,20,100,12"', b''))
        self.assertEqual(manifest["fragments"][0]["locator_granularity"], "unavailable")

    def test_pdf01_preflight_bytes_pages_and_encryption(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        stream = BytesIO()
        writer.write(stream)
        pdf = stream.getvalue()
        digest = sha256(pdf).hexdigest()
        self.assertEqual(verify_pdf(pdf, digest), 1)
        for code, kwargs in (("INPUT_LIMIT", {"max_bytes": 1}), ("PAGE_LIMIT", {"max_pages": 0})):
            with self.assertRaisesRegex(TransformationError, code):
                verify_pdf(pdf, digest, **kwargs)
        with self.assertRaisesRegex(TransformationError, "INTEGRITY_ERROR"):
            verify_pdf(pdf, "b" * 64)
        corrupt = b"%PDF-1.7\n%%EOF"
        with self.assertRaisesRegex(TransformationError, "CORRUPT_PDF"):
            verify_pdf(corrupt, sha256(corrupt).hexdigest())
        writer.encrypt("fixture")
        encrypted = BytesIO()
        writer.write(encrypted)
        with self.assertRaisesRegex(TransformationError, "ENCRYPTED_PDF"):
            verify_pdf(encrypted.getvalue(), sha256(encrypted.getvalue()).hexdigest())

    def test_pdf01_parser_errors_remain_distinct_and_coordinates_requested(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        stream = BytesIO()
        writer.write(stream)
        pdf = stream.getvalue()
        client = Grobid("http://127.0.0.1:18070")
        for status, body, expected in ((204, b"", "NO_TEXT"), (500, b"NO_BLOCKS", "NO_TEXT"),
                                      (500, b"PARSING_ERROR", "PARSER_REJECTED"),
                                      (500, b"TIMEOUT", "TIMEOUT"), (503, b"busy", "TRANSIENT_PARSER_FAILURE")):
            with patch.object(client, "request", side_effect=[(200, b'{"version":"0.9.1","revision":"0.9.1"}'), (status, body)]):
                with self.assertRaisesRegex(TransformationError, expected):
                    client.extract(pdf, sha256(pdf).hexdigest())
        with patch.object(client, "request", side_effect=[(200, b'{"version":"0.9.1","revision":"0.9.1"}'), (200, TEI)]) as call:
            self.assertEqual(client.extract(pdf, sha256(pdf).hexdigest()), (TEI, 1))
            wire = call.call_args.args[2]
            self.assertEqual(wire.count(b'name="teiCoordinates"'), 2)
        for version in (b'{"version":"0.9.0"}', b'broken', b'[]', b'null'):
            with patch.object(client, "request", return_value=(200, version)):
                with self.assertRaisesRegex(TransformationError, "PARSER_VERSION_MISMATCH"):
                    client.extract(pdf, sha256(pdf).hexdigest())


if __name__ == "__main__":
    unittest.main()
