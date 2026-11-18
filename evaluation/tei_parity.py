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
    result = {"space": space.digest, "query_vector_set": rows.digest,
              **compare(np.asarray(served), rows.take(np.arange(rows.n), np.float64))}
    print(json.dumps(result))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
