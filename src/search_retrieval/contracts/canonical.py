"""One canonical JSON serialization for every hashed identity and manifest."""

from hashlib import sha256
import json
from math import isfinite
import re


SHA256 = re.compile(r"[0-9a-f]{64}")


def _check(value, path: str) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"Non-finite number at {path}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"Non-string key at {path}")
            _check(item, f"{path}.{key}")
        return
    raise ValueError(f"Unsupported {type(value).__name__} at {path}")


def canonical_json(value) -> bytes:
    """UTF-8 JSON with sorted keys and no insignificant whitespace.

    Integers and floats stay distinct (`1` and `1.0` hash differently). Text is
    not Unicode-normalized: callers own the normalization of their strings.
    """
    _check(value, "$")
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sealed_digest(kind: str, body) -> str:
    """Hash a typed body. The kind keeps equal bodies of different types apart."""
    if type(kind) is not str or not kind:
        raise ValueError("A sealed digest requires a kind")
    return sha256(canonical_json({"kind": kind, "schema_version": 1, "body": body})).hexdigest()


def is_sha256(value) -> bool:
    return type(value) is str and SHA256.fullmatch(value) is not None
