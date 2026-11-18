"""Project a canonical vector set and its corpus text into the Vespa `passage` collection.

    python evaluation/vespa_project.py CONFIG --vectors-dir DIR --data-dir DIR --out-dir DIR \
        --vespa-url http://localhost:8080 [--text-transform MANIFEST] [--threads 16]

CONFIG is an ANN sweep TOML (config/ann/): its inputs name the space, corpus, transform and
queries. Rows are fed in collection order with the document IDs of the vector set; a document
whose ID differs from its vector row stops the feed. The feed resumes from its last finished
window. When it ends, the collection count must equal the corpus size, and a fixed sample of
documents read back must hold exactly the canonical float32 rows, text and cohort bucket.

The collection label is the digest of the vector set, the corpus text transform and the
application package, so another schema or another vector set is another collection.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np

from search_retrieval.adapters.vespa_passages import Passage, VespaPassages
from search_retrieval.contracts.canonical import sealed_digest
from search_retrieval.contracts.cohorts import cohort_bucket
from search_retrieval.experiment.manifests import ManifestError, inside, read_manifest, require, seal
from search_retrieval.experiment.provenance import ProvenanceError, _git, verify_clean, verify_repository
from search_retrieval.experiment.run_config import CORPUS, TRANSFORM, ConfigError
from search_retrieval.experiment.textmode import rerun_in_utf8_mode
from search_retrieval.query.eligible import SearchUnavailable
from ann import open_sets
from ann_sweep import parse_sweep
from embed import ROOT, iterate_documents
from vector_store import _write_atomic, in_parity_sample


APPLICATION = ROOT / "infra/vespa/app"
SAMPLE = 200


def application_digest() -> str:
    """Digest of every file of the Vespa application package."""
    digest = sha256()
    for path in sorted(item for item in APPLICATION.rglob("*") if item.is_file()):
        data = path.read_bytes()
        digest.update(f"{path.relative_to(APPLICATION).as_posix()}\t{len(data)}\n".encode("utf-8"))
        digest.update(data)
    return digest.hexdigest()


def collection_label(name: str, documents_digest: str, corpus_digest: str, text_digest: str, app: str) -> str:
    body = {"vector_set": documents_digest, "corpus": corpus_digest, "text_transform": text_digest, "application": app}
    safe = "".join(character if character.isalnum() else "-" for character in name.lower()).strip("-")
    return f"{safe}-{sealed_digest('vespa-projection', body)[:16]}"


def passages(documents, corpus: dict, text_transform: dict, data_dir: Path, start: int):
    """Yield the passages from `start` on, checking that text and vector rows describe the same document."""
    for position, (item, title, text) in enumerate(iterate_documents(corpus, text_transform, data_dir)):
        if position < start:
            continue
        if documents.ids[position] != item:
            raise ManifestError(f"Row {position} of the vector set is {documents.ids[position]}, the corpus has {item}")
        body = (title + " " + text) if title else text
        vector = documents.take(np.array([position]), np.float32)[0]
        yield Passage(item, body, vector.tolist(), cohort_bucket(item))


def sample_positions(documents) -> list[int]:
    every = max(1, documents.n // SAMPLE)
    return [position for position, item in enumerate(documents.ids) if in_parity_sample(item, every)]


def verify_sample(client: VespaPassages, label: str, documents, texts: dict) -> dict:
    """Read back a fixed sample and compare it with the canonical rows."""
    positions = sample_positions(documents)
    mismatches = []
    for position in positions:
        item = documents.ids[position]
        fields = client.get(label, item)
        stored = np.asarray(fields.get("embedding"), dtype=np.float32)
        expected = documents.take(np.array([position]), np.float32)[0]
        if (stored.shape != expected.shape or not np.array_equal(stored, expected)
                or fields.get("cohort_bucket") != cohort_bucket(item) or fields.get("text") != texts[item]):
            mismatches.append(item)
    return {"sampled": len(positions), "mismatches": mismatches[:20], "mismatch_count": len(mismatches)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config")
    parser.add_argument("--vectors-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--vespa-url", required=True)
    parser.add_argument("--text-transform", default="manifests/transform/beir-title-space-text-v1.json",
                        help="how the corpus becomes the BM25 text: title + space + text, or the passage alone")
    parser.add_argument("--threads", type=int, default=16)
    args = parser.parse_args()
    rerun = rerun_in_utf8_mode()
    if rerun is not None:
        return rerun
    try:
        verify_repository(ROOT)
        verify_clean(ROOT)
        config = parse_sweep(inside(ROOT, args.config).read_bytes())
        corpus, corpus_digest = read_manifest(ROOT, config["inputs"]["corpus"], "corpus")
        require(corpus, "corpus", *CORPUS)
        text_transform, text_digest = read_manifest(ROOT, args.text_transform, "transform")
        require(text_transform, "transform", TRANSFORM)
        documents, _ = open_sets(config, args.vectors_dir)
        app = application_digest()
        label = collection_label(corpus["name"], documents.digest, corpus_digest, text_digest, app)
        directory = args.out_dir / label
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / "manifest.json").exists():
            print(json.dumps({"collection": label, "reused": True}))
            return 0
        checkpoint = directory / "fed.json"
        start = json.loads(checkpoint.read_bytes())["rows"] if checkpoint.exists() else 0
        client = VespaPassages(args.vespa_url, timeout=60)

        reported = [start]

        def progress(fed):
            _write_atomic(checkpoint, json.dumps({"rows": start + fed}).encode())
            if start + fed - reported[0] >= 100_000:
                reported[0] = start + fed
                print(f"fed {start + fed} / {documents.n}", flush=True)

        fed = client.feed(label, passages(documents, corpus, text_transform, args.data_dir, start),
                          threads=args.threads, progress=progress)
        _write_atomic(checkpoint, json.dumps({"rows": start + fed}).encode())
        count = client.count(label)
        if count != documents.n:
            raise SearchUnavailable(f"Collection {label} holds {count} documents, expected {documents.n}")
        wanted = {documents.ids[position] for position in sample_positions(documents)}
        texts = {item: ((title + " " + text) if title else text)
                 for item, title, text in iterate_documents(corpus, text_transform, args.data_dir) if item in wanted}
        sample = verify_sample(client, label, documents, texts)
        if sample["mismatch_count"]:
            raise SearchUnavailable(f"{sample['mismatch_count']} sampled documents differ from the canonical rows")
        body = {"collection": label, "documents": documents.n, "vector_set": documents.digest, "corpus": corpus_digest,
                "text_transform": text_digest, "application": app, "space": documents.space, "sample": sample,
                "acl_tokens": ["public"], "code_commit": _git(ROOT, "rev-parse", "HEAD")}
        sealed = seal("vespa-projection", body)
        _write_atomic(directory / "manifest.json", (json.dumps(sealed, indent=2) + "\n").encode())
        documents.close()
        print(json.dumps({"collection": label, "documents": documents.n, "sample": sample["sampled"]}))
        return 0
    except (ConfigError, ManifestError, ProvenanceError, SearchUnavailable, ValueError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
