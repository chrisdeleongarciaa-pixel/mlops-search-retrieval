"""Step 8: choose the fusion on development runs, offline, and test whether it beats its branches.

    python evaluation/fusion_sweep.py select LEXICAL_RUN DENSE_RUN --data-dir DIR --out FILE
    python evaluation/fusion_sweep.py gate FUSED_PER_QUERY BRANCH_PER_QUERY [BRANCH_PER_QUERY] --out FILE

`select` reads two finished development runs of the same query set (TREC files at depth 1,000)
and scores RRF (k = 60) and convex fusion with min-max scaling for alpha in 0, 0.05, ..., 1.
Nothing queries the engine. The choice maximises mean nDCG@10; a tie goes to RRF, then to the
smaller alpha. It is compared with the better branch by a paired bootstrap over queries.

`gate` applies the promotion rule to any split, typically the one test report: the fusion is
promoted only if the lower bound of the paired 95 % interval against the better branch is
above zero.
"""

import argparse
import json
from pathlib import Path
import sys

from search_retrieval.experiment.outputs import parse_trec_run, verify_outputs
from search_retrieval.experiment.run_config import resolve
from search_retrieval.query.ranking import Ranked, convex, fuse
from bench import FORMATS, ROOT
from measures import aggregate, evaluate
from paired_bootstrap import paired_interval, read_scores


ALPHAS = [round(step * 0.05, 2) for step in range(21)]
MEASURE = "nDCG@10"
DEPTH = 1000


def branch(directory: Path) -> tuple[dict, dict]:
    """A finished run's rankings for every query it answered, including those without results.

    run.trec has no line for a query without hits, while per-query.jsonl has one record per
    query; a query absent from the TREC file is an empty ranking (it scores zero, as in bench.py).
    """
    manifest = verify_outputs(directory)
    hits = parse_trec_run((directory / "run.trec").read_bytes())
    with (directory / "per-query.jsonl").open(encoding="utf-8") as stream:
        queries = [json.loads(line)["qid"] for line in stream if line.strip()]
    if len(queries) != manifest["queries"] or len(set(queries)) != len(queries) or not hits.keys() <= set(queries):
        raise ValueError(f"The TREC run and the per-query records of {directory} disagree")
    return manifest, {qid: hits.get(qid, []) for qid in queries}


def fused(lexical: dict, dense: dict, method: dict) -> dict[str, list[tuple[str, float]]]:
    result = {}
    for qid in lexical:
        left = [Ranked(doc, score) for doc, score in lexical[qid]]
        right = [Ranked(doc, score) for doc, score in dense.get(qid, [])]
        run = fuse(left, right, depth=DEPTH) if method["kind"] == "rrf" else convex(left, right, alpha=method["alpha"], depth=DEPTH)
        result[qid] = [(hit.id, hit.score) for hit in run.fused[:DEPTH]]
    return result


def choose(scored: list[dict]) -> dict:
    """The highest mean; among equals RRF first, then the smallest alpha."""
    def key(row):
        method = row["method"]
        return (-row[MEASURE], 0 if method["kind"] == "rrf" else 1, method.get("alpha", 0.0))
    return min(scored, key=key)


def select(lexical_dir: Path, dense_dir: Path, data_dir: Path) -> dict:
    lexical_manifest, lexical = branch(lexical_dir)
    dense_manifest, dense = branch(dense_dir)
    if lexical_manifest["keys"]["query_set"] != dense_manifest["keys"]["query_set"]:
        raise ValueError("The branches ran different query sets")
    if lexical_manifest["split"] != "dev" or dense_manifest["split"] != "dev":
        raise ValueError("Fusion is chosen on development runs only")
    for manifest in (lexical_manifest, dense_manifest):
        if manifest["configuration"]["values"]["ranking"]["target_hits"] != DEPTH:
            raise ValueError(f"Branches are read at depth {DEPTH}")
    if lexical.keys() != dense.keys():
        raise ValueError("The branches answered different queries")
    resolved = resolve(ROOT, lexical_manifest["configuration"]["path"])
    judgments = FORMATS[resolved.corpus["format"]](resolved, data_dir).judgments()
    candidates = [{"kind": "rrf", "k": 60}] + [{"kind": "convex", "alpha": alpha, "normalization": "minmax"} for alpha in ALPHAS]
    scored = []
    per_query = {}
    for method in candidates:
        scores = evaluate(fused(lexical, dense, method), judgments)
        label = "rrf" if method["kind"] == "rrf" else f"convex-{method['alpha']:.2f}"
        per_query[label] = {qid: row[MEASURE] for qid, row in scores.items()}
        scored.append({"label": label, "method": method, **aggregate(scores)})
    branches = {}
    for name, run in (("lexical", lexical), ("dense", dense)):
        scores = evaluate(run, judgments)
        per_query[name] = {qid: row[MEASURE] for qid, row in scores.items()}
        branches[name] = aggregate(scores)
    best = choose(scored)
    better_branch = max(branches, key=lambda name: branches[name][MEASURE])
    interval = paired_interval(per_query[best["label"]], per_query[better_branch])
    return {
        "measure": MEASURE, "queries": len(lexical), "lexical_run": lexical_manifest["keys"]["run"],
        "dense_run": dense_manifest["keys"]["run"], "candidates": scored, "branches": branches,
        "chosen": best, "better_branch": better_branch, "chosen_minus_branch": interval,
        "promoted_on_dev": interval["left_minus_right"]["ci95"][0] > 0,
        "per_query": per_query,
    }


def gate(fused_file: Path, branch_files: list[Path]) -> dict:
    fused_scores = read_scores([fused_file], "run", MEASURE)
    branches = {path.parent.name: read_scores([path], "run", MEASURE) for path in branch_files}
    better = max(branches, key=lambda name: sum(branches[name].values()) / len(branches[name]))
    interval = paired_interval(fused_scores, branches[better])
    return {"measure": MEASURE, "better_branch": better, "fused_minus_branch": interval,
            "promoted": interval["left_minus_right"]["ci95"][0] > 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    chooser = commands.add_parser("select")
    chooser.add_argument("lexical", type=Path)
    chooser.add_argument("dense", type=Path)
    chooser.add_argument("--data-dir", type=Path, required=True)
    chooser.add_argument("--out", type=Path, required=True)
    checker = commands.add_parser("gate")
    checker.add_argument("fused", type=Path)
    checker.add_argument("branches", type=Path, nargs="+")
    checker.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = select(args.lexical, args.dense, args.data_dir) if args.command == "select" else gate(args.fused, args.branches)
    except (ValueError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shown = {key: value for key, value in result.items() if key not in ("per_query", "candidates")}
    print(json.dumps(shown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
