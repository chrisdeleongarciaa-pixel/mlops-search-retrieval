"""Validation boundary shared by offline Qwen and Gemma rewrite experiments."""

import json


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"action": {"type": "string", "enum": ["keep", "rewrite"]},
                   "rewritten_text": {"type": "string", "minLength": 1, "maxLength": 8192}},
    "required": ["action", "rewritten_text"],
}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate rewrite field")
        value[key] = item
    return value


def validate_rewrite(original: str, payload: str, *, required_literals=(), finish_reason="stop"):
    """Check schema and explicit literals. Semantic fidelity needs annotated judgments."""
    if finish_reason != "stop":
        raise ValueError("Incomplete rewrite generation")
    if not isinstance(original, str) or not original.strip() or len(original) > 8192:
        raise ValueError("Invalid original query")
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > 65536:
        raise ValueError("Invalid rewrite payload")
    value = json.loads(payload, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {"action", "rewritten_text"}:
        raise ValueError("Rewrite schema mismatch")
    text = value["rewritten_text"]
    if (value["action"] not in ("keep", "rewrite") or not isinstance(text, str)
            or not text.strip() or len(text) > 8192):
        raise ValueError("Invalid rewrite action or text")
    if value["action"] == "keep" and text != original:
        raise ValueError("Keep action changed the query")
    for literal in required_literals:
        if not isinstance(literal, str) or not literal or literal not in original or literal not in text:
            raise ValueError("Explicit query literal lost or invalid")
    return text
