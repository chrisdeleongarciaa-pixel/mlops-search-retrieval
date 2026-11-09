"""Estimate paired query-level score differences with a percentile bootstrap."""

import argparse
import gzip
import json
from math import isfinite
from pathlib import Path
from random import Random
from statistics import fmean


def read_scores(paths, ranker, metric):
    builds = []
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as source:
            scores = {qid: row[metric] for qid, row in json.load(source)[ranker].items()}
        if not scores or any(type(value) not in (float, int) or not isfinite(value)
                             for value in scores.values()):
            raise ValueError(f"Expected finite per-query scores: {path}")
        if builds and scores.keys() != builds[0].keys():
            raise ValueError("Builds must have identical query IDs")
        builds.append(scores)
    return {qid: fmean(build[qid] for build in builds) for qid in builds[0]}


def quantile(sorted_values, probability):
    position = probability * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def paired_interval(left, right, *, seed=20261108, resamples=10_000):
    """Resample whole matched queries. Builds, if any, are averaged within query."""
    if not left or left.keys() != right.keys() or resamples < 2:
        raise ValueError("Expected identical nonempty query IDs and at least two resamples")
    qids = sorted(left)
    a, b = [left[qid] for qid in qids], [right[qid] for qid in qids]
    differences = [x - y for x, y in zip(a, b, strict=True)]
    rng = Random(seed)
    samples = [[], [], []]
    for _ in range(resamples):
        draw = rng.choices(range(len(qids)), k=len(qids))
        for distribution, values in zip(samples, (a, b, differences), strict=True):
            distribution.append(fmean(values[index] for index in draw))
    intervals = []
    for distribution in samples:
        distribution.sort()
        intervals.append([quantile(distribution, p) for p in (0.025, 0.975)])
    return {
        "method": "paired percentile bootstrap; query is the sampling unit",
        "queries": len(qids), "resamples": resamples, "seed": seed,
        "left": {"mean": fmean(a), "ci95": intervals[0]},
        "right": {"mean": fmean(b), "ci95": intervals[1]},
        "left_minus_right": {"mean": fmean(differences), "ci95": intervals[2]},
        "wins_ties_losses": [sum(delta > 0 for delta in differences),
                             sum(delta == 0 for delta in differences),
                             sum(delta < 0 for delta in differences)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for side in ("left", "right"):
        parser.add_argument(f"--{side}", type=Path, action="append", required=True)
        parser.add_argument(f"--{side}-ranker", required=True)
    parser.add_argument("--metric", default="ndcg_cut_10")
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--resamples", type=int, default=10_000)
    args = parser.parse_args()
    left = read_scores(args.left, args.left_ranker, args.metric)
    right = read_scores(args.right, args.right_ranker, args.metric)
    result = paired_interval(left, right, seed=args.seed, resamples=args.resamples)
    print(json.dumps({
        "metric": args.metric, "left_ranker": args.left_ranker, "right_ranker": args.right_ranker,
        "left_builds": len(args.left), "right_builds": len(args.right), **result,
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
