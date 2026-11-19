"""Download the pinned model snapshot of a registered space and verify every file.

    python evaluation/fetch_model.py SPACE OUTPUT_DIR

Files come from the Hugging Face revision named by the space. A gated model needs an access
token in the HF_TOKEN environment variable; the token is sent only to huggingface.co.
"""

import argparse
from hashlib import file_digest
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

from search_retrieval.adapters.space_encoder import verify_snapshot
from search_retrieval.experiment.run_config import read_space


ROOT = Path(__file__).resolve().parents[1]
HOST = "https://huggingface.co"


def file_url(encoder: str, revision: str, name: str) -> str:
    if encoder.count("/") != 1 or ".." in name or name.startswith("/"):
        raise ValueError("Expected an organization/model name and a relative file name")
    return f"{HOST}/{quote(encoder)}/resolve/{quote(revision)}/{quote(name)}"


def fetch(url: str, path: Path, checksum: str, token: str | None) -> bool:
    """Download one file unless a verified copy exists. Returns whether it was downloaded."""
    if path.is_file():
        with path.open("rb") as source:
            if file_digest(source, "sha256").hexdigest() == checksum:
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    request = Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urlopen(request, timeout=120) as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, 1 << 20)
    with temporary.open("rb") as source:
        observed = file_digest(source, "sha256").hexdigest()
    if observed != checksum:
        temporary.unlink()
        raise ValueError(f"Downloaded file does not match the space: {path.name}")
    temporary.replace(path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("space")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    body, _ = read_space(ROOT, args.space)
    token = os.environ.get("HF_TOKEN")
    try:
        for name, checksum in sorted(body["files"].items()):
            downloaded = fetch(file_url(body["encoder"], body["revision"], name), args.output / name, checksum, token)
            print(f"{'downloaded' if downloaded else 'verified'}: {name}", flush=True)
        verify_snapshot(args.output, body["files"])
    except (OSError, ValueError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
