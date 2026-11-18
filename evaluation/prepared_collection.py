"""A collection already materialized as JSON lines, such as the output of prepare_litsearch.py.

The corpus manifest names a directory under the data directory and the SHA-256 of its corpus
file. A query-set manifest names its query file and its judgment file with their hashes.
Judgments are a TSV of (query-id, corpus-id, score), or the `corpusids` of each query row when
the judgment file is the query file itself (LitSearch development queries carry their
positives that way).
"""

from csv import DictReader
from hashlib import file_digest
from io import StringIO
import json
from pathlib import Path

from search_retrieval.experiment.manifests import ManifestError


FORMAT = "prepared-jsonl"


def _verified(path: Path, expected: str) -> bytes:
    try:
        with path.open("rb") as source:
            if file_digest(source, "sha256").hexdigest() != expected:
                raise ManifestError(f"{path.name} does not match its manifest")
        return path.read_bytes()
    except OSError as error:
        raise ManifestError(f"Missing collection file: {path}") from error


def _need(body: dict, kind: str, *names: str) -> None:
    missing = [name for name in names if name not in body]
    if missing:
        raise ManifestError(f"A {FORMAT} {kind} manifest requires {missing}")


def documents(corpus: dict, data_dir: Path):
    """Yield `(id, title, abstract)` in file order."""
    _need(corpus, "corpus", "directory", "corpus_file", "documents_sha256")
    data = _verified(data_dir / corpus["directory"] / corpus["corpus_file"], corpus["documents_sha256"])
    for line in data.decode("utf-8").splitlines():
        row = json.loads(line)
        yield str(row["id"]), row.get("title") or "", row.get("abstract") or ""


def queries(corpus: dict, query_set: dict, data_dir: Path) -> dict[str, str]:
    _need(corpus, "corpus", "directory")
    _need(query_set, "query-set", "queries_file")
    data = _verified(data_dir / corpus["directory"] / query_set["queries_file"], query_set["queries_sha256"])
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    result = {row["id"]: row["query"] for row in rows}
    if len(result) != len(rows):
        raise ManifestError("Duplicate query IDs")
    return result


def judgments(corpus: dict, query_set: dict, data_dir: Path) -> dict[str, dict[str, int]]:
    _need(query_set, "query-set", "qrels_file")
    path = data_dir / corpus["directory"] / query_set["qrels_file"]
    data = _verified(path, query_set["qrels_sha256"]).decode("utf-8")
    result: dict[str, dict[str, int]] = {}
    if path.suffix == ".jsonl":
        for row in map(json.loads, data.splitlines()):
            result[row["id"]] = {str(doc): 1 for doc in row["corpusids"]}
    else:
        for row in DictReader(StringIO(data), delimiter="\t"):
            result.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    return result
