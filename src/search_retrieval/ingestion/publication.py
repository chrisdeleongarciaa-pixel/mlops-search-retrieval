"""Projection worker for the bounded UTF-8 witness profile."""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol
from uuid import UUID

from search_retrieval.ingestion.accept_revision import IntegrityError, Operation


@dataclass(frozen=True, slots=True)
class Work:
    event_id: UUID
    token: UUID
    generation: str
    document_key: int
    tenant: str
    sequence: int
    operation: Operation
    reference: str | None
    size: int | None
    digest: str | None


@dataclass(frozen=True, slots=True)
class Fragment:
    fragment_id: str
    text: str
    start: int
    end: int
    sha256: str


class Originals(Protocol):
    def read_verified(self, reference: str, size: int, digest: str) -> bytes: ...


class PublicationLedger(Protocol):
    def claim(self) -> Work | None: ...
    def reserve(self, projection: str, digest: str, manifest: dict) -> None: ...
    def finish(
        self, work: Work, outcome: str, projection: str | None = None
    ) -> str: ...


class ProjectionIndex(Protocol):
    def ensure_visible(
        self, work: Work, projection: str, fragments: tuple[Fragment, ...]
    ) -> None: ...


def project(work: Work, raw: bytes) -> tuple[str, tuple[Fragment, ...], str, dict]:
    """Use original UTF-8 byte offsets and stable, line-based fragments."""
    identity = [work.document_key, work.sequence, work.generation, "utf8-lines-v1"]
    projection = sha256(json.dumps(identity).encode("ascii")).hexdigest()
    fragments = []
    exclusions = []
    start = 0
    for ordinal, line in enumerate(raw.splitlines(keepends=True)):
        end = start + len(line)
        text = line.decode("utf-8")
        if text.strip():
            fragments.append(Fragment(
                f"{projection}-{ordinal}", text, start, end, sha256(line).hexdigest()
            ))
        else:
            exclusions.append({"start": start, "end": end, "reason": "blank-line"})
        start = end
    if not fragments:
        raise ValueError("No searchable UTF-8 fragments")
    manifest = {
        "identity": identity, "input_sha256": work.digest, "offset_unit": "utf8-bytes",
        "original_ref": work.reference, "exclusions": exclusions,
        "fragments": [
            {"id": f.fragment_id, "start": f.start, "end": f.end, "sha256": f.sha256}
            for f in fragments
        ],
    }
    encoded = json.dumps(
        manifest,
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return projection, tuple(fragments), sha256(encoded).hexdigest(), manifest


class PublicationWorker:
    def __init__(
        self, ledger: PublicationLedger, originals: Originals, index: ProjectionIndex, *,
        max_original_bytes: int = 1_048_576,
    ) -> None:
        if max_original_bytes <= 0:
            raise ValueError("Invalid publication byte limit")
        self.ledger = ledger
        self.originals = originals
        self.index = index
        self.max_original_bytes = max_original_bytes

    def run_one(self) -> str | None:
        """Persist a terminal outcome only after verification or a permanent rejection."""
        work = self.ledger.claim()
        if work is None:
            return None
        if work.operation == Operation.WITHDRAW:
            return self.ledger.finish(work, "withdrawn")
        try:
            if work.reference is None or work.size is None or work.digest is None:
                raise IntegrityError("Accepted UPSERT is missing its original")
            if work.size > self.max_original_bytes:
                raise ValueError("Original exceeds witness transformation limit")
            raw = self.originals.read_verified(work.reference, work.size, work.digest)
            projection, fragments, digest, manifest = project(work, raw)
        except (ValueError, IntegrityError):
            return self.ledger.finish(work, "quarantined")
        self.ledger.reserve(projection, digest, manifest)
        self.index.ensure_visible(work, projection, fragments)
        return self.ledger.finish(work, "published", projection)
