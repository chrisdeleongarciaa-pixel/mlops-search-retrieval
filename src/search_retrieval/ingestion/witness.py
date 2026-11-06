"""Replay the immutable laboratory feed and checkpoint contiguous decisions."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Protocol

from search_retrieval.ingestion.accept_revision import AuthorizedSource, Failure, Receipt
from search_retrieval.ingestion.submit_event import EventSubmission


class Checkpoints(Protocol):
    def checkpoint(self, digest: str) -> int: ...
    def advance_checkpoint(self, digest: str, cursor: int) -> None: ...


@dataclass(frozen=True, slots=True)
class ReplayResult:
    cursor: int
    stopped: Failure | None = None
    missing_cursor: int | None = None


def replay_witness(
    submission: EventSubmission,
    checkpoints: Checkpoints,
    feed: Path,
    corpus: Path,
    *,
    delivery: tuple[int, ...] | None = None,
) -> ReplayResult:
    """The operator supplies a trusted feed and the exact corpus it references."""
    feed_bytes = feed.read_bytes()
    corpus_bytes = corpus.read_bytes()
    feed_digest = sha256(feed_bytes).hexdigest()
    manifest = json.loads(feed_bytes)
    corpus_digest = sha256(corpus_bytes).hexdigest()
    if manifest["version"] != 1 or manifest["corpus_sha256"] != corpus_digest:
        raise ValueError("Witness corpus does not match the feed manifest")
    docs = json.loads(corpus_bytes)["documents"]
    events = {event["cursor"]: event for event in manifest["events"]}
    ordered = tuple(range(1, len(manifest["events"]) + 1))
    if set(events) != set(ordered) or any(type(cursor) is not int for cursor in events):
        raise ValueError("Witness feed must contain a unique event at every cursor")

    saved = checkpoints.checkpoint(feed_digest)
    for cursor in ordered if delivery is None else delivery:
        if cursor <= saved:
            continue
        if cursor != saved + 1:
            return ReplayResult(saved, missing_cursor=saved + 1)
        event = {key: value for key, value in events[cursor].items() if key != "cursor"}
        doc = event["document_id"]
        scope = AuthorizedSource(event["tenant_id"], event["source_id"])
        original = None
        if event["operation"] == "UPSERT":
            item = docs[doc]
            if (
                item["tenant"] != scope.tenant_id
                or event["original_ref"] != f"witness:{corpus_digest}/{doc}"
            ):
                raise ValueError("Witness event is outside its authorized source")
            payload = item["text"].encode("utf-8")
            if event["original_version"] != sha256(payload).hexdigest():
                raise ValueError("Witness version does not match its bytes")
            original = BytesIO(payload)
        result = submission.submit_event(scope, event, original)
        if not isinstance(result, Receipt) and result != Failure.STALE_REVISION:
            return ReplayResult(saved, stopped=result)
        checkpoints.advance_checkpoint(feed_digest, cursor)
        saved = cursor
    return ReplayResult(saved)
