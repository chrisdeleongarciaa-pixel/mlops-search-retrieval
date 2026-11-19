"""Percentiles of latency samples with distribution-free confidence intervals.

The q-quantile is the order statistic of rank ceil(q n). Its 95 % interval is the pair of
order statistics whose ranks bracket the binomial(n, q) count of samples below the true
quantile with at least 95 % probability, computed exactly (no normal approximation).
"""

from math import ceil, lgamma, log

import numpy as np


def _binomial_cdf(n: int, p: float) -> np.ndarray:
    """P(X <= k) for k = 0..n, X ~ binomial(n, p), in log space for large n."""
    k = np.arange(n + 1)
    logs = np.array([lgamma(n + 1) - lgamma(i + 1) - lgamma(n - i + 1) for i in range(n + 1)])
    logs = logs + k * log(p) + (n - k) * log(1 - p)
    top = logs.max()
    weights = np.exp(logs - top)
    return np.cumsum(weights) / weights.sum()


def quantile_interval(samples: np.ndarray, q: float, confidence: float = 0.95) -> dict:
    """Point estimate and interval of the q-quantile, with the ranks used (1-based)."""
    values = np.sort(np.asarray(samples, dtype=np.float64))
    n = values.size
    if n == 0 or not 0 < q < 1:
        raise ValueError("Need samples and a quantile strictly between 0 and 1")
    rank = max(1, ceil(q * n))
    cdf = _binomial_cdf(n, q)
    tail = (1 - confidence) / 2
    # Lower rank l: the largest with P(X <= l - 1) <= tail; upper u: the smallest with P(X <= u - 1) >= 1 - tail.
    lower = int(np.searchsorted(cdf, tail, side="right"))
    upper = int(np.searchsorted(cdf, 1 - tail, side="left")) + 1
    bounded = lower >= 1 and upper <= n
    return {"value": float(values[rank - 1]), "rank": rank,
            "low": float(values[lower - 1]) if lower >= 1 else None,
            "high": float(values[upper - 1]) if upper <= n else None,
            "ranks": [lower, upper], "bounded": bounded}


def summary(samples: np.ndarray, quantiles=(0.5, 0.95, 0.99)) -> dict:
    values = np.asarray(samples, dtype=np.float64)
    result = {"n": int(values.size), "mean": float(values.mean()), "max": float(values.max())}
    for q in quantiles:
        result[f"p{round(q * 100):g}"] = quantile_interval(values, q)
    return result


def exponential_arrivals(rate: float, count: int, seed: int) -> np.ndarray:
    """Arrival offsets in seconds of a Poisson process of `rate` per second."""
    if rate <= 0 or count <= 0:
        raise ValueError("Rate and count must be positive")
    return np.random.default_rng(seed).exponential(1 / rate, count).cumsum()

