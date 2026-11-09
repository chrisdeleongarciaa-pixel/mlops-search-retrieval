"""Bounded client for the pinned GROBID scientific PDF profile."""

from hashlib import sha256
import http.client
import json
from io import BytesIO
import socket
import time
from urllib.parse import urlsplit
from uuid import uuid4

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from search_retrieval.ingestion.transformation import TransformationError


IMAGE = "lfoppiano/grobid@sha256:eb306e6d494f6f7e89b35bbaf3b4925afd58c6a5638c775f2a1c35bfd3c5db0d"
OPTIONS = (("consolidateHeader", "0"), ("consolidateCitations", "0"),
           ("includeRawCitations", "0"), ("teiCoordinates", "head"), ("teiCoordinates", "p"))


def verify_pdf(payload: bytes, expected_sha256: str, *, max_bytes=20 * 1024 * 1024, max_pages=200):
    if len(payload) > max_bytes:
        raise TransformationError("INPUT_LIMIT")
    if sha256(payload).hexdigest() != expected_sha256:
        raise TransformationError("INTEGRITY_ERROR")
    if not payload.startswith(b"%PDF-"):
        raise TransformationError("UNSUPPORTED_FORMAT")
    try:
        reader = PdfReader(BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise TransformationError("ENCRYPTED_PDF")
        pages = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as error:
        if isinstance(error, TransformationError):
            raise
        raise TransformationError("CORRUPT_PDF") from error
    if pages < 1:
        raise TransformationError("CORRUPT_PDF")
    if pages > max_pages:
        raise TransformationError("PAGE_LIMIT")
    return pages


class Grobid:
    def __init__(self, endpoint: str, *, timeout=90.0, max_response_bytes=8 * 1024 * 1024):
        address = urlsplit(endpoint)
        if (address.scheme not in {"http", "https"} or not address.hostname
                or address.username or address.password or address.query or address.fragment
                or address.path not in {"", "/"} or not 0 < timeout <= 300 or max_response_bytes <= 0):
            raise ValueError("Invalid GROBID endpoint or limits")
        self.address, self.timeout, self.max_response_bytes = address, timeout, max_response_bytes

    def request(self, method, path, body=None, headers=None):
        connection_type = http.client.HTTPSConnection if self.address.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self.address.hostname, self.address.port, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            content = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransformationError("TIMEOUT")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                block = response.read1(min(65536, self.max_response_bytes + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
                if len(content) > self.max_response_bytes:
                    raise TransformationError("OUTPUT_LIMIT")
            return response.status, bytes(content)
        except (TimeoutError, socket.timeout) as error:
            raise TransformationError("TIMEOUT") from error
        except (OSError, http.client.HTTPException) as error:
            raise TransformationError("TRANSIENT_PARSER_FAILURE") from error
        finally:
            connection.close()

    def extract(self, pdf: bytes, expected_sha256: str):
        pages = verify_pdf(pdf, expected_sha256)
        status, version = self.request("GET", "/api/version")
        try:
            version_info = json.loads(version)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransformationError("PARSER_VERSION_MISMATCH") from error
        if status != 200 or not isinstance(version_info, dict) or version_info.get("version") != "0.9.1":
            raise TransformationError("PARSER_VERSION_MISMATCH")
        boundary = "grobid-" + uuid4().hex
        parts = []
        for name, value in OPTIONS:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="input"; filename="document.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),
            pdf, f'\r\n--{boundary}--\r\n'.encode(),
        ])
        status, tei = self.request("POST", "/api/processFulltextDocument", b"".join(parts),
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if status == 204:
            raise TransformationError("NO_TEXT")
        if status != 200:
            for marker, code in ((b"NO_BLOCKS", "NO_TEXT"), (b"TOO_MANY_BLOCKS", "PARSER_LIMIT"),
                                 (b"TOO_MANY_TOKENS", "PARSER_LIMIT"), (b"TIMEOUT", "TIMEOUT"),
                                 (b"PARSING_ERROR", "PARSER_REJECTED"), (b"BAD_INPUT_DATA", "PARSER_REJECTED")):
                if marker in tei:
                    raise TransformationError(code)
            raise TransformationError("TRANSIENT_PARSER_FAILURE")
        return tei, pages
