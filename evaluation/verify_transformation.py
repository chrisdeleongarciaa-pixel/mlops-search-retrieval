"""Independent byte-range and provenance verifier for PDF-03 artifacts."""

import argparse
from hashlib import sha256
import json
from pathlib import Path


def verify(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    text = (directory / "canonical.txt").read_bytes()
    tei = (directory / "extraction.tei.xml").read_bytes()
    if (manifest["status"] != "complete" or manifest["offset_unit"] != "utf8-bytes"
            or sha256(text).hexdigest() != manifest["canonical_sha256"]
            or sha256(tei).hexdigest() != manifest["tei_sha256"]):
        raise ValueError("Artifact integrity failure")
    previous = 0
    seen = set()
    for fragment in manifest["fragments"]:
        start, end = fragment["start"], fragment["end"]
        if not 0 <= previous <= start < end <= len(text) or text[previous:start].strip():
            raise ValueError("Invalid offset or missing canonical content")
        payload = text[start:end]
        payload.decode("utf-8", errors="strict")
        if (sha256(payload).hexdigest() != fragment["sha256"] or fragment["id"] in seen
                or not 0 < fragment["tokens"] <= manifest["recipe"]["max_tokens"]):
            raise ValueError("Invalid fragment digest, identity or token budget")
        seen.add(fragment["id"])
        previous = end
    if not seen or text[previous:].strip():
        raise ValueError("Incomplete canonical content coverage")
    return {"status": "verified", "fragments": len(seen), "canonical_bytes": len(text),
            "missing_locators": sum(not f["boxes"] for f in manifest["fragments"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify(parser.parse_args().directory)))
