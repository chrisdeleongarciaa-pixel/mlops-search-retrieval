"""Compare LitSearch runs with the published baselines, in the paper's own measures.

    python evaluation/litsearch_compare.py NAME=RUN_DIR [NAME=RUN_DIR ...] --queries MANIFEST \
        --corpus MANIFEST --data-dir DIR [--out FILE]

LitSearch (Ajith et al., 2024, arXiv:2407.18940v2, Table 3) reports recall@20 on broad questions
and recall@5 on specific questions, averaged over its 597 questions with each question weighted
equally. The workbench reports nDCG@10 on its own 300-question test split, a sample stratified by
question set and specificity (prepare_litsearch.py), so the paper's numbers are compared here
on that split: the same measures, per question, with a percentile bootstrap interval over the
questions (10,000 resamples, seed 20261119). The split is a sample of the paper's questions, not
the whole set; the paper's systems ran on all 597.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

from search_retrieval.experiment.manifests import read_manifest
from search_retrieval.experiment.outputs import parse_trec_run
import prepared_collection
from embed import ROOT


# Table 3 of arXiv:2407.18940v2 (read 2026-11-19): "Avg." columns, titles and abstracts only.
PUBLISHED = {
    "BM25": {"broad_R@20": 39.9, "specific_R@5": 50.0},
    "GTR-T5-large": {"broad_R@20": 43.8, "specific_R@5": 39.6},
    "Instructor-XL": {"broad_R@20": 56.5, "specific_R@5": 52.3},
    "E5-large-v2": {"broad_R@20": 55.4, "specific_R@5": 56.2},
    "GritLM-7B": {"broad_R@20": 70.8, "specific_R@5": 74.8},
    "GPT-4o reranking (w/ GritLM)": {"broad_R@20": 75.3, "specific_R@5": 79.2},
}
SEED, RESAMPLES = 20261119, 10_000


def recall(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant)


def interval(values: np.ndarray) -> list[float]:
    rng = np.random.default_rng(SEED)
    means = values[rng.integers(0, values.size, (RESAMPLES, values.size))].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def score(run: dict, questions: list[dict]) -> dict:
    """Per-question recall in the paper's measures; a question without results scores zero."""
    broad, specific = [], []
    for question in questions:
        ranked = [doc for doc, _ in run.get(question["id"], [])]
        relevant = set(question["corpusids"])
        if question["specificity"] == 0:
            broad.append(recall(ranked, relevant, 20))
        elif question["specificity"] == 1:
            specific.append(recall(ranked, relevant, 5))
        else:
            raise ValueError(f"Unknown specificity for {question['id']}")
    result = {}
    for name, values in (("broad_R@20", broad), ("specific_R@5", specific)):
        array = np.array(values) * 100
        result[name] = {"mean": float(array.mean()), "ci95": interval(array), "questions": int(array.size)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="NAME=RUN_DIR with a run.trec")
    parser.add_argument("--queries", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    corpus, _ = read_manifest(ROOT, args.corpus, "corpus")
    query_set, _ = read_manifest(ROOT, args.queries, "query-set")
    path = args.data_dir / corpus["directory"] / query_set["queries_file"]
    texts = prepared_collection.queries(corpus, query_set, args.data_dir)  # verifies the file's hash
    questions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if {question["id"] for question in questions} != set(texts):
        print("The queries file and its manifest disagree", file=sys.stderr)
        return 1
    systems = {}
    for item in args.runs:
        name, _, directory = item.partition("=")
        systems[name] = score(parse_trec_run((Path(directory) / "run.trec").read_bytes()), questions)
    report = {"split": query_set["name"], "questions": len(questions), "systems": systems, "published": PUBLISHED,
              "published_source": "Ajith et al., LitSearch, arXiv:2407.18940v2, Table 3 (597 questions)",
              "note": "The split is a stratified sample of the paper's questions; the published systems ran on all 597."}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
