"""Rank metrics through ir_measures, including the fraction of judged results."""

from statistics import fmean


# Reported for every run. Judged@10 exposes holes that the "unjudged is non-relevant" default hides.
MEASURES = ("nDCG@10", "RR@10", "R@10", "R@100", "R@1000", "Judged@10")


def evaluate(rankings: dict[str, list[tuple[str, float]]], judgments: dict[str, dict[str, int]],
             measures: tuple[str, ...] = MEASURES) -> dict[str, dict[str, float]]:
    """Score each ranked query. The order of each list is the ranking.

    Reciprocal ranks replace the scores so that the evaluator cannot reorder tied results.
    A query without results scores zero on every measure.
    """
    import ir_measures

    unjudged = rankings.keys() - judgments.keys()
    if unjudged:
        raise ValueError(f"Queries without judgments: {sorted(unjudged)[:5]}")
    try:
        parsed = [ir_measures.parse_measure(name) for name in measures]
    except NameError as error:
        raise ValueError(f"Measure names must be in ir_measures canonical form: {error}") from error
    if [str(measure) for measure in parsed] != list(measures):
        raise ValueError("Measure names must be in ir_measures canonical form")
    run = {qid: {doc: 1 / rank for rank, (doc, _) in enumerate(hits, 1)} for qid, hits in rankings.items() if hits}
    if any(len(run[qid]) != len(rankings[qid]) for qid in run):
        raise ValueError("A ranking repeats a document")
    scores = {qid: {} if hits else dict.fromkeys(measures, 0.0) for qid, hits in rankings.items()}
    if run:
        for metric in ir_measures.iter_calc(parsed, {qid: judgments[qid] for qid in run}, run):
            scores[metric.query_id][str(metric.measure)] = float(metric.value)
    incomplete = [qid for qid, row in scores.items() if set(row) != set(measures)]
    if incomplete:
        raise ValueError(f"The evaluator did not score every measure for {incomplete[:5]}")
    return scores


def aggregate(scores: dict[str, dict[str, float]]) -> dict[str, float]:
    """Arithmetic mean over all scored queries."""
    if not scores:
        raise ValueError("No scored queries")
    return {name: fmean(row[name] for row in scores.values()) for name in sorted(next(iter(scores.values())))}
