"""Deterministic TEI-to-chunk transformation for accepted scientific PDFs."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import re
from typing import Callable

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from search_retrieval.contracts.identity import RevisionKey


TEI = "{http://www.tei-c.org/ns/1.0}"


class TransformationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Recipe:
    parser: str
    tokenizer: str
    generation: str
    max_tokens: int = 512
    max_tei_bytes: int = 8 * 1024 * 1024
    max_blocks: int = 10000
    normalization: str = "tei-head-p-whitespace-v1"

    def __post_init__(self):
        if not all(isinstance(value, str) and value.strip() for value in (
            self.parser, self.tokenizer, self.generation
        )):
            raise ValueError("Recipe identities must be nonempty strings")
        for value in (self.max_tokens, self.max_tei_bytes, self.max_blocks):
            if type(value) is not int or value <= 0:
                raise ValueError("Recipe limits must be positive integers")
        if self.normalization != "tei-head-p-whitespace-v1":
            raise ValueError("Unsupported normalization")


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def coordinates(element):
    result = []
    for box in filter(None, element.get("coords", "").split(";")):
        try:
            page, x, y, width, height = map(float, box.split(","))
        except ValueError as error:
            raise TransformationError("INVALID_COORDINATES") from error
        if (not all(math.isfinite(v) for v in (page, x, y, width, height))
                or page < 1 or not page.is_integer() or min(x, y, width, height) < 0):
            raise TransformationError("INVALID_COORDINATES")
        result.append({"page": int(page), "x": x, "y": y, "width": width, "height": height})
    return result


def extract_blocks(tei: bytes, recipe: Recipe):
    if len(tei) > recipe.max_tei_bytes:
        raise TransformationError("OUTPUT_LIMIT")
    try:
        root = ElementTree.fromstring(tei, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as error:
        raise TransformationError("INVALID_TEI") from error
    body = root.find(f"{TEI}text/{TEI}body")
    if root.tag != TEI + "TEI" or body is None:
        raise TransformationError("INCOMPLETE_TEI")
    blocks, exclusions = [], []

    def walk(container, sections):
        current = sections
        for element in container:
            kind = element.tag.removeprefix(TEI)
            if kind in {"figure", "table", "formula", "listBibl", "note"}:
                exclusions.append({"kind": kind, "reason": "outside-profile"})
                continue
            if kind in {"head", "p"}:
                text = " ".join("".join(element.itertext()).split())
                if not text:
                    continue
                if kind == "head":
                    current = (*sections, text)
                blocks.append({"kind": kind, "text": text, "sections": list(current),
                               "boxes": coordinates(element)})
                if len(blocks) > recipe.max_blocks:
                    raise TransformationError("BLOCK_LIMIT")
            else:
                walk(element, current)

    abstract = root.find(f"{TEI}teiHeader/{TEI}profileDesc/{TEI}abstract")
    if abstract is not None:
        walk(abstract, ("Abstract",))
    walk(body, ())
    if root.find(f"{TEI}text/{TEI}back") is not None:
        exclusions.append({"kind": "back", "reason": "outside-profile"})
    if not blocks:
        raise TransformationError("NO_TEXT")
    return blocks, exclusions


def split_block(text: str, count_tokens: Callable[[str], int], limit: int):
    """Preserve contiguous text without assuming monotonic BPE token counts."""
    def count(value):
        size = count_tokens(value)
        if type(size) is not int or size < 1:
            raise TransformationError("INVALID_TOKEN_COUNT")
        return size

    total = count(text)
    if total <= limit:
        return [(text, total)]
    pieces = []
    start = 0
    end = 0
    for match in re.finditer(r"\S+\s*", text):
        candidate_end = match.end()
        if count(text[start:candidate_end]) > limit:
            if end == start:
                raise TransformationError("UNSPLITTABLE_TOKEN_BUDGET")
            pieces.append((text[start:end], count(text[start:end])))
            start = end
            if count(text[start:candidate_end]) > limit:
                raise TransformationError("UNSPLITTABLE_TOKEN_BUDGET")
        end = candidate_end
    pieces.append((text[start:end], count(text[start:end])))
    return pieces


def transform_revision(revision: RevisionKey, original_sha256: str, tei: bytes,
                       recipe: Recipe, count_tokens: Callable[[str], int]):
    """PDF-02..04. Caller has verified the durable original before parsing."""
    if (type(revision.sequence) is not int or revision.sequence <= 0
            or not all(isinstance(v, str) and v.strip() for v in asdict(revision.document).values())
            or not isinstance(original_sha256, str) or not re.fullmatch("[a-f0-9]{64}", original_sha256)):
        raise ValueError("Invalid durable revision identity")
    blocks, exclusions = extract_blocks(tei, recipe)
    text = "\n\n".join(block["text"] for block in blocks).encode("utf-8")
    identity = {"revision": asdict(revision), "original_sha256": original_sha256,
                "tei_sha256": sha256(tei).hexdigest(),
                "recipe": asdict(recipe), "canonical_sha256": sha256(text).hexdigest()}
    transformation_id = sha256(canonical_json(identity)).hexdigest()
    fragments = []
    offset = 0
    for block_number, block in enumerate(blocks):
        for piece, tokens in split_block(block["text"], count_tokens, recipe.max_tokens):
            encoded = piece.encode("utf-8")
            fragments.append({
                "id": f"{transformation_id}-{len(fragments)}", "block": block_number,
                "start": offset, "end": offset + len(encoded), "sha256": sha256(encoded).hexdigest(),
                "tokens": tokens, "sections": block["sections"], "boxes": block["boxes"],
                "locator_granularity": "source-element" if block["boxes"] else "unavailable",
            })
            offset += len(encoded)
        offset += 2
    manifest = {"schema_version": 1, "status": "complete", **identity,
                "transformation_id": transformation_id,
                "offset_unit": "utf8-bytes", "fragments": fragments, "exclusions": exclusions}
    return text, manifest
