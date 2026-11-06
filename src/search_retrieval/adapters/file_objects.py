"""Immutable objects on a dedicated, persistent local filesystem."""

from contextlib import suppress
from hashlib import sha256
import json
import os
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from search_retrieval.ingestion.accept_revision import (
    DependencyFailure,
    IntegrityError,
    RevisionCommand,
)


class FileObjectStore:
    """Store per-document objects without replacing existing bytes.

    The configured directory must exist and support same-directory hard links.
    Its lifecycle and write access are controlled by the deployment.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def read_verified(self, reference: str, size: int, digest: str) -> bytes:
        """Read an internally recorded reference and verify the exact bytes returned."""
        if (
            len(reference) != 129 or reference[64:] != "-" + digest
            or any(char not in "0123456789abcdef" for char in reference[:64])
            or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise IntegrityError("Invalid immutable reference")
        try:
            with (self._root / reference).open("rb") as source:
                payload = source.read(size + 1)
        except OSError as error:
            raise DependencyFailure("Could not read accepted original") from error
        if len(payload) != size or sha256(payload).hexdigest() != digest:
            raise IntegrityError("Accepted original no longer matches its recorded bytes")
        return payload

    def put_verified(self, command: RevisionCommand, original: BinaryIO) -> str:
        expected_size = command.original_size
        expected_digest = command.original_sha256
        if expected_size is None or expected_digest is None:
            raise ValueError("The object store requires a validated UPSERT original")
        document = command.document
        identity = json.dumps([document.tenant_id, document.source_id, document.document_id])
        prefix = sha256(identity.encode("ascii")).hexdigest()
        name = f"{prefix}-{expected_digest}"
        target = self._root / name
        stage = None
        try:
            if target.exists():
                self._verify(target, expected_size, expected_digest)
            else:
                with NamedTemporaryFile(dir=self._root, prefix=".stage-", delete=False) as file:
                    stage = Path(file.name)
                    copyfileobj(original, file, length=64 * 1024)
                    file.flush()
                    os.fsync(file.fileno())
                self._verify(stage, expected_size, expected_digest)
                try:
                    os.link(stage, target)
                except FileExistsError:
                    self._verify(target, expected_size, expected_digest)
            if os.name == "posix":
                directory = os.open(self._root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return name
        except OSError as error:
            raise DependencyFailure("Original store I/O failed") from error
        finally:
            if stage is not None:
                with suppress(OSError):
                    stage.unlink()

    @staticmethod
    def _verify(target: Path, expected_size: int, expected_digest: str) -> None:
        digest = sha256()
        size = 0
        with target.open("rb") as stored:
            for chunk in iter(lambda: stored.read(64 * 1024), b""):
                size += len(chunk)
                if size > expected_size:
                    raise IntegrityError("Persisted original has unexpected size")
                digest.update(chunk)
        if size != expected_size or digest.hexdigest() != expected_digest:
            raise IntegrityError("Persisted original has unexpected size or checksum")
