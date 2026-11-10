"""Run the lexical reference on the complete SciFact BEIR test split."""

import argparse
from collections import Counter, defaultdict
import csv
from hashlib import file_digest, sha256
from io import StringIO
import json
from math import log1p
from pathlib import Path
import platform
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile


DATASET_SHA256 = "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"
EVALUATOR_SHA256 = "f346bd20f258e552895e438257e3cc91308b65e0b1ee6c09422e987084bc3bd0"
EVALUATOR_REVISION = "ef83d29307061c65d04b035b4f4e7c18bd8374af"
EVALUATOR_ARCHIVE_SHA256 = "90cd84030cffdcdcf27187fd2954a4cfd6014d8747a2da8c3cb509038bc04ed4"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def terms(text: str) -> Counter:
    return Counter(re.findall(r"[^\W_]+", text.casefold()))


def bm25(corpus: dict[str, str], queries: dict[str, str]) -> dict[str, dict[str, float]]:
    postings = defaultdict(list)
    lengths = {}
    for doc, text in corpus.items():
        counts = terms(text)
        lengths[doc] = counts.total()
        for term, frequency in counts.items():
            postings[term].append((doc, frequency))
    average = sum(lengths.values()) / len(lengths)
    runs = {}
    for qid, text in queries.items():
        scores = defaultdict(float)
        for term in terms(text):
            matches = postings.get(term, ())
            idf = log1p((len(corpus) - len(matches) + .5) / (len(matches) + .5))
            for doc, frequency in matches:
                norm = 1.2 * (.25 + .75 * lengths[doc] / average)
                scores[doc] += idf * frequency * 2.2 / (frequency + norm)
        runs[qid] = dict(sorted(scores.items(), key=lambda hit: (-hit[1], hit[0]))[:100])
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("evaluator_archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if digest(args.dataset) != DATASET_SHA256:
        raise ValueError("SciFact archive checksum mismatch")
    if digest(args.evaluator_archive) != EVALUATOR_ARCHIVE_SHA256:
        raise ValueError("BEIR archive checksum mismatch")
    # The archive is read in place. Reserve extra room for raw runs and evidence.
    if shutil.disk_usage(args.output.parent).free < 64 * 1024 * 1024:
        raise OSError("Insufficient disk space for the SciFact run")

    with TemporaryDirectory() as work:
        root = Path(work).resolve()
        with ZipFile(args.evaluator_archive) as source:
            if any(not (root / name).resolve().is_relative_to(root) for name in source.namelist()):
                raise ValueError("Invalid evaluator archive path")
            source.extractall(root)
        repository = root / f"beir-{EVALUATOR_REVISION}"
        evaluator = repository / "beir/retrieval/evaluation.py"
        if digest(evaluator) != EVALUATOR_SHA256:
            raise ValueError("Evaluator file does not match the pinned BEIR commit")
        sys.path.insert(0, str(repository))
        execute(args, evaluator)


def execute(args, evaluator: Path):
    import beir.retrieval.evaluation as official
    import pytrec_eval

    if Path(official.__file__).resolve() != evaluator:
        raise ValueError("Resolved evaluator does not match verified source")
    inputs = {}
    with ZipFile(args.dataset) as archive:
        for name in ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"):
            inputs[name] = archive.read("scifact/" + name)
    corpus = {
        doc["_id"]: doc.get("title", "") + " " + doc["text"]
        for doc in map(json.loads, inputs["corpus.jsonl"].splitlines())
    }
    queries = {
        query["_id"]: query["text"]
        for query in map(json.loads, inputs["queries.jsonl"].splitlines())
    }
    judgments = defaultdict(dict)
    for row in csv.DictReader(StringIO(inputs["qrels/test.tsv"].decode()), delimiter="\t"):
        judgments[row["query-id"]][row["corpus-id"]] = int(row["score"])
    if not judgments or not set(judgments) <= queries.keys() or any(
        not set(labels) <= corpus.keys() for labels in judgments.values()
    ):
        raise ValueError("SciFact qrels do not match corpus or query IDs")
    results = bm25(corpus, {qid: queries[qid] for qid in judgments})
    # Preserve the documented ID tie break when the evaluator sorts by score.
    ranked = {
        qid: {doc: 1 / rank for rank, doc in enumerate(hits, 1)}
        for qid, hits in results.items()
    }
    ndcg, _, recall, _ = official.EvaluateRetrieval.evaluate(
        judgments, {qid: dict(hits) for qid, hits in ranked.items()}, [10, 100],
        ignore_identical_ids=True,
    )
    filtered = {qid: {doc: score for doc, score in hits.items() if doc != qid}
                for qid, hits in ranked.items()}
    scores = pytrec_eval.RelevanceEvaluator(judgments, {"ndcg_cut.10", "recall.100"}).evaluate(filtered)
    if scores.keys() != judgments.keys():
        raise ValueError("Official evaluator did not score the entire test split")
    args.output.mkdir(exist_ok=False)
    manifest = {
        "dataset": "BEIR/SciFact", "split": "test", "dataset_sha256": DATASET_SHA256,
        "dataset_url": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
        "license_source": "https://github.com/allenai/scifact/blob/master/LICENSE.md",
        "input_hashes": {name: sha256(data).hexdigest() for name, data in inputs.items()},
        "evaluator_revision": EVALUATOR_REVISION, "evaluator_sha256": EVALUATOR_SHA256,
        "evaluator_archive_sha256": EVALUATOR_ARCHIVE_SHA256,
        "dependency_lock_sha256": digest(Path(__file__).resolve().parents[1] / "config/requirements-eval-win64.lock"),
        "code_sha256": digest(Path(__file__)),
        "python": platform.python_version(), "platform": platform.platform(),
        "ranker": "reference-bm25-unicode-casefold", "k1": 1.2, "b": .75,
        "depth": 100, "ignore_identical_ids": True,
        "corpus_documents": len(corpus), "expected_queries": len(judgments),
        "scored_queries": len(scores), "empty_queries": sum(not hits for hits in filtered.values()),
        "metrics": {"NDCG@10": ndcg["NDCG@10"], "Recall@100": recall["Recall@100"]},
    }
    for name, value in (("manifest", manifest), ("run", results), ("per-query", scores)):
        (args.output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
