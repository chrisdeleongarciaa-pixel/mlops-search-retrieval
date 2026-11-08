"""Recalculate frozen SciFact rankings using the verified official BEIR source."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess

from scifact_bm25 import digest, EVALUATOR_REVISION
from scifact_dense import evaluate_rankings
from scifact_embeddings import load_split


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "evaluator", "rankings", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    inputs = {"schema_version": 1, "purpose": "frozen-ranking-recalculation", "evaluator_revision": EVALUATOR_REVISION,
              "ignore_identical_ids": True, "python": platform.python_version(), "platform": platform.platform(),
              "files": {name: {"path": str(getattr(args, name).resolve()), "sha256": digest(getattr(args, name))}
                        for name in ("dataset", "evaluator", "rankings")},
              "lock_sha256": digest(root / "config/requirements-eval-win64.lock"),
              "code_sha256": {name: digest(root / "evaluation" / name) for name in (
                  "reproduce_scifact.py", "scifact_dense.py", "scifact_bm25.py", "scifact_embeddings.py")},
              "packages": {name: version(name) for name in ("pytrec-eval-terrier", "numpy", "scipy", "tqdm", "colorama")}}
    inputs["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    (args.output / "inputs.json").write_text(json.dumps(inputs, indent=2)+"\n", encoding="utf-8")
    try:
        _, queries, qrels = load_split(args.dataset, "test")
        rankings = json.loads(args.rankings.read_text(encoding="utf-8"))
        if set(rankings) != set(queries):
            raise ValueError("Incomplete frozen ranking query coverage")
        metrics, per_query = evaluate_rankings({"bm25": rankings}, qrels, args.evaluator)
        observed = metrics["bm25"]
        expected = {"NDCG@10": .66168, "Recall@10": .79094, "Recall@100": .88589}
        if observed != expected:
            raise ValueError("Frozen reference metric mismatch")
        (args.output / "per-query.json").write_text(json.dumps(per_query, indent=2)+"\n", encoding="utf-8")
        result = {"status": "complete", "queries": len(queries), "metrics": observed,
                  "per_query_sha256": digest(args.output / "per-query.json")}
    except Exception as error:
        result = {"status": "failed", "error": type(error).__name__, "detail": str(error)}
    (args.output / "result.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result))
    return int(result["status"] != "complete")


if __name__ == "__main__":
    raise SystemExit(main())
