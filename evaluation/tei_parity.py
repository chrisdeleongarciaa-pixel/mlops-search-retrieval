"""TEI gate: query vectors served online equal the canonical batch vectors of the same space.

    python evaluation/tei_parity.py --tei-url URL --space MANIFEST --corpus MANIFEST --queries MANIFEST \
        --transform MANIFEST --vectors-dir DIR --data-dir DIR [--batch 32]

The canonical rows were written by `embed.py encode` with the reference implementation. Every
query is encoded by TEI with the same rendered prompt; the gate requires, for every query, a
maximum absolute difference of at most 1e-5 and a cosine of at least 0.99999, the tolerance of
the adapter conformance check.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

from search_retrieval.adapters.tei import TeiEncoder, TeiSpace
from search_retrieval.experiment.manifests import read_manifest, require
from search_retrieval.experiment.run_config import QUERY_SET, read_space
from search_retrieval.experiment.textmode import rerun_in_utf8_mode
from canonical import CanonicalRows
from embed import CONFORMANCE, ROOT, iterate_queries, set_directory
from vector_store import Identity
import prepared_collection


def top_k(queries: np.ndarray, documents: np.ndarray, k: int) -> np.ndarray:
    """Positions of the k best documents per query by float64 inner product, ties by position."""
    scores = queries.astype(np.float64) @ documents.astype(np.float64).T
    return np.argsort(-scores, axis=1, kind="stable")[:, :k]


def ndcg_at(ranked: list[str], relevant: set[str], k: int) -> float:
    """Binary-relevance nDCG@k with log2 discounts."""
    gain = sum(1.0 / np.log2(rank + 2) for rank, doc in enumerate(ranked[:k]) if doc in relevant)
    ideal = sum(1.0 / np.log2(rank + 2) for rank in range(min(k, len(relevant))))
    return gain / ideal if ideal else 0.0


def ranking_impact(served: np.ndarray, canonical: np.ndarray, documents: np.ndarray, doc_ids: list[str],
                   query_ids: list[str], judgments: dict[str, dict[str, int]], k: int = 10) -> dict:
    """What the served vectors change in exact retrieval over the corpus, against the canonical ones.

    A diagnostic beside the vector gate: whether a difference above the vector tolerance moves
    any document in or out of the top k, and what it does to nDCG@k on the judged queries.
    """
    served_top, canonical_top = top_k(served, documents, k), top_k(canonical, documents, k)
    overlap = np.array([len(set(a) & set(b)) / k for a, b in zip(served_top, canonical_top)])
    identical = np.array([np.array_equal(a, b) for a, b in zip(served_top, canonical_top)])
    deltas = []
    for row, query in enumerate(query_ids):
        relevant = {doc for doc, grade in judgments.get(query, {}).items() if grade > 0}
        if relevant:
            deltas.append(ndcg_at([doc_ids[p] for p in served_top[row]], relevant, k)
                          - ndcg_at([doc_ids[p] for p in canonical_top[row]], relevant, k))
    deltas = np.array(deltas)
    return {"k": k, "queries": int(len(query_ids)), "identical_top_k": float(identical.mean()),
            "mean_overlap_at_k": float(overlap.mean()), "min_overlap_at_k": float(overlap.min()),
            "judged_queries": int(deltas.size),
            "mean_ndcg_delta": float(deltas.mean()) if deltas.size else None,
            "max_abs_ndcg_delta": float(np.abs(deltas).max()) if deltas.size else None}


def compare(served: np.ndarray, canonical: np.ndarray) -> dict:
    served, canonical = served.astype(np.float64), canonical.astype(np.float64)
    errors = np.abs(served - canonical).max(axis=1)
    cosines = (served * canonical).sum(axis=1) / (np.linalg.norm(served, axis=1) * np.linalg.norm(canonical, axis=1))
    return {"queries": int(served.shape[0]), "max_absolute_error": float(errors.max()), "min_cosine": float(cosines.min()),
            "queries_over_error_limit": int((errors > CONFORMANCE["max_absolute_error"]).sum()),
            "limits": CONFORMANCE,
            "passed": bool(errors.max() <= CONFORMANCE["max_absolute_error"] and cosines.min() >= CONFORMANCE["min_cosine"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("tei-url", "space", "corpus", "queries", "transform"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--vectors-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--batch", type=int, default=32)
    args = parser.parse_args()
    rerun = rerun_in_utf8_mode()
    if rerun is not None:
        return rerun
    body, space = read_space(ROOT, args.space)
    corpus, _ = read_manifest(ROOT, args.corpus, "corpus")
    query_set, _ = read_manifest(ROOT, args.queries, "query-set")
    require(query_set, "query-set", *QUERY_SET)
    rows = CanonicalRows(set_directory(args.vectors_dir, space.digest, Identity(
        space, "query", {"queries": query_set["queries_sha256"]})))
    texts = {item: text for item, _, text in iterate_queries(query_set, corpus, args.data_dir)}
    if list(texts) != rows.ids:
        print("The query vector set and the query file disagree", file=sys.stderr)
        return 1
    encoder = TeiEncoder(args.tei_url, TeiSpace.from_body(body), timeout=30)
    served = []
    for start in range(0, rows.n, args.batch):
        served += encoder.encode_queries([texts[item] for item in rows.ids[start:start + args.batch]])
    served = np.asarray(served)
    canonical = rows.take(np.arange(rows.n), np.float64)
    result = {"space": space.digest, "query_vector_set": rows.digest, **compare(served, canonical)}
    # Ranking impact over the corpus's canonical document vectors (exact search, float64).
    _, corpus_digest = read_manifest(ROOT, args.corpus, "corpus")
    _, transform_digest = read_manifest(ROOT, args.transform, "transform")
    documents = CanonicalRows(set_directory(args.vectors_dir, space.digest, Identity(
        space, "document", {"corpus": corpus_digest, "transform": transform_digest})))
    judged = prepared_collection.judgments(corpus, query_set, args.data_dir)
    result["ranking"] = ranking_impact(served, canonical, documents.take(np.arange(documents.n), np.float32),
                                       documents.ids, rows.ids, judged)
    print(json.dumps(result))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
