"""Small statistics helpers for the detectors (standard library only)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

Row = dict[str, Any]  # one row of `AnalyticsDB.query`


def group_by(rows: Iterable[Row], *keys: str) -> dict[tuple[Any, ...], list[Row]]:
    """Rows grouped by the values of `keys`, in first-seen order."""
    groups: dict[tuple[Any, ...], list[Row]] = {}
    for row in rows:
        groups.setdefault(tuple(row[k] for k in keys), []).append(row)
    return groups


def _two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def two_proportion_p(hits_a: int, n_a: int, hits_b: int, n_b: int) -> float:
    """Two-sided p-value of the two-proportion z-test (is hits_a / n_a different from hits_b / n_b?)."""
    if n_a == 0 or n_b == 0:
        return 1.0
    pooled = (hits_a + hits_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 1.0
    return _two_sided_p((hits_a / n_a - hits_b / n_b) / se)


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for k in range(start, end + 1):
            ranks[order[k]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def mann_whitney_p(a: Sequence[float], b: Sequence[float]) -> float:
    """Two-sided p-value of the Mann-Whitney U test (normal approximation, no tie correction)."""
    n_a, n_b = len(a), len(b)
    if n_a == 0 or n_b == 0:
        return 1.0
    ranks = _average_ranks([*a, *b])
    u = sum(ranks[:n_a]) - n_a * (n_a + 1) / 2
    sd = math.sqrt(n_a * n_b * (n_a + n_b + 1) / 12)
    return _two_sided_p((u - n_a * n_b / 2) / sd)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval of a proportion (well-behaved at 0 and 1, unlike the Wald interval)."""
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def difference_ci(hits_a: int, n_a: int, hits_b: int, n_b: int) -> tuple[float, float]:
    """95% interval of hits_a / n_a - hits_b / n_b (Newcombe's hybrid score method)."""
    p_a, p_b = hits_a / n_a, hits_b / n_b
    low_a, high_a = wilson(hits_a, n_a)
    low_b, high_b = wilson(hits_b, n_b)
    diff = p_a - p_b
    return diff - math.hypot(p_a - low_a, high_b - p_b), diff + math.hypot(high_a - p_a, p_b - low_b)
