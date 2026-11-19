"""Sealed JSON manifests: a typed body and the digest that identifies it."""

import json
from pathlib import Path

from search_retrieval.contracts.canonical import sealed_digest


class ManifestError(ValueError):
    """A manifest is missing, malformed or does not match its own digest."""


def seal(kind: str, body: dict) -> dict:
    return {"kind": kind, "schema_version": 1, "body": body, "sha256": sealed_digest(kind, body)}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"Duplicate key in manifest: {key}")
        result[key] = value
    return result


def inside(root: Path, relative: str) -> Path:
    """Resolve a repository-relative reference without leaving the repository."""
    if type(relative) is not str or not relative or Path(relative).is_absolute():
        raise ManifestError(f"Expected a repository-relative path: {relative!r}")
    base = root.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ManifestError(f"Reference leaves the repository: {relative}")
    return path


def parse_manifest(data: bytes, kind: str) -> tuple[dict, str]:
    """Return the body and its verified digest."""
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeDecodeError, ValueError) as error:
        raise ManifestError(f"Invalid manifest: {error}") from error
    if not isinstance(document, dict) or set(document) != {"kind", "schema_version", "body", "sha256"}:
        raise ManifestError("A manifest has exactly kind, schema_version, body and sha256")
    if document["kind"] != kind:
        raise ManifestError(f"Expected a {kind} manifest, found {document['kind']!r}")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ManifestError("Unsupported manifest schema version")
    if not isinstance(document["body"], dict):
        raise ManifestError("A manifest body is an object")
    try:
        expected = sealed_digest(kind, document["body"])
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if document["sha256"] != expected:
        raise ManifestError(f"The {kind} manifest does not match its digest")
    return document["body"], expected


def read_manifest(root: Path, relative: str, kind: str) -> tuple[dict, str]:
    path = inside(root, relative)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ManifestError(f"Unresolved {kind} manifest: {relative}") from error
    try:
        return parse_manifest(data, kind)
    except ManifestError as error:
        raise ManifestError(f"{relative}: {error}") from error


def require(body: dict, kind: str, required: dict, optional: dict | None = None) -> None:
    """Reject unknown, missing and mistyped manifest fields."""
    optional = optional or {}
    unknown = set(body) - set(required) - set(optional)
    missing = set(required) - set(body)
    if unknown or missing:
        raise ManifestError(f"{kind}: unknown {sorted(unknown)}, missing {sorted(missing)}")
    for name, value in body.items():
        if type(value) is not {**required, **optional}[name]:
            raise ManifestError(f"{kind}.{name} has the wrong type")
