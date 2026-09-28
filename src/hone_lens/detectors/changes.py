"""The `regression` detector: a metric that changed significantly at a version boundary (prompt version,
model, or step code version).

Latency / step duration and scores are compared with the Mann-Whitney U test, error rates with the
two-proportion z-test, at significance level `ALPHA` (set `hone_lens.detectors.changes.ALPHA` to be
stricter or looser).
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from statistics import mean, median
from typing import Any

from hone_lens.findings import Finding, finding, pct, where
from hone_lens.stats import Row, group_by, mann_whitney_p, two_proportion_p
from hone_lens.store import AnalyticsDB

ALPHA = 0.01
MIN_RUNS = 20
LATENCY_RATIO = 1.2
ERROR_RATE_RISE = 0.02
SCORE_DROP = 0.05
FAILED = ("error", "failed")  # call / span status `error`; hone-flow step status `failed`
Scope = dict[str, Any]


def regression(db: AnalyticsDB) -> list[Finding]:
    """Compare the runs just before and just after every version change."""
    calls = db.query(
        "SELECT span_id, start_time, run_id, workflow, step, scorer, model, template_version, latency_ms, "
        "status FROM calls WHERE replay_of IS NULL ORDER BY start_time, span_id"
    )
    steps = db.query(
        "SELECT span_id, start_time, workflow, step, step_version, duration_ms AS latency_ms, status "
        "FROM steps WHERE executed = 1 ORDER BY start_time, span_id"
    )
    prompt_of_run = {
        c["run_id"]: c["template_version"] for c in calls if c["template_version"] and not c["scorer"]
    }
    scores = [
        {**r, "prompt_version": prompt_of_run.get(r["run_id"])}
        for r in db.query(
            "SELECT span_id, start_time, run_id, workflow, scorer, value FROM selections "
            "WHERE kind = 'score' AND value IS NOT NULL ORDER BY start_time, span_id"
        )
    ]
    # (rows, boundary label, column that changes, columns that stay fixed, checks)
    series: list[tuple[list[Row], str, str, tuple[str, ...], tuple[Check, ...]]] = [
        (
            calls,
            "prompt_version",
            "template_version",
            ("workflow", "step", "scorer", "model"),
            (_slower, _errors),
        ),
        (calls, "model", "model", ("workflow", "step", "scorer"), (_slower, _errors)),
        (steps, "code_version", "step_version", ("workflow", "step"), (_slower, _errors)),
        (scores, "prompt_version", "prompt_version", ("workflow", "scorer"), (_lower_scores,)),
    ]
    findings: list[Finding] = []
    for rows, label, column, fixed, checks in series:
        for key, group in group_by(rows, *fixed).items():
            by_version = group_by([r for r in group if r[column] is not None], column)  # first-seen order
            for ((before,), old), ((after,), new) in pairwise(by_version.items()):
                if min(len(old), len(new)) < MIN_RUNS:
                    continue
                scope = {**dict(zip(fixed, key, strict=True)), label: after}
                boundary = {"field": label, "from": before, "to": after, "at": new[0]["start_time"]}
                for check in checks:
                    findings += check(scope, boundary, old, new, len(group))
    return findings


Check = Callable[[Scope, Scope, list[Row], list[Row], int], list[Finding]]


def _slower(scope: Scope, boundary: Scope, old: list[Row], new: list[Row], total: int) -> list[Finding]:
    a = [r["latency_ms"] for r in old if r["latency_ms"] is not None]
    b = [r["latency_ms"] for r in new if r["latency_ms"] is not None]
    if not a or not b or not median(a):
        return []
    before, after = median(a), median(b)
    ratio = after / before
    p = mann_whitney_p(a, b)
    if ratio < LATENCY_RATIO or p >= ALPHA:
        return []
    return [
        finding(
            f"Latency of {where(scope)} up {pct(ratio - 1)} after {_name(boundary)} "
            f"(median {before:.0f} -> {after:.0f} ms)",
            category="regression",
            scope=scope,
            affected=new,
            total=total,
            severity="high" if ratio >= 1.5 else "medium",
            metric={"name": "latency_ms_median", "value": after, "baseline": before, "p_value": p},
            details={"boundary": boundary},
        )
    ]


def _errors(scope: Scope, boundary: Scope, old: list[Row], new: list[Row], total: int) -> list[Finding]:
    errors_old = sum(r["status"] in FAILED for r in old)
    failed = [r for r in new if r["status"] in FAILED]
    before, after = errors_old / len(old), len(failed) / len(new)
    p = two_proportion_p(errors_old, len(old), len(failed), len(new))
    if after - before < ERROR_RATE_RISE or p >= ALPHA:
        return []
    return [
        finding(
            f"Error rate of {where(scope)} rose from {pct(before)} to {pct(after)} after {_name(boundary)}",
            category="regression",
            scope=scope,
            affected=failed,
            total=total,
            severity="high" if after - before >= 0.1 else "medium",
            metric={"name": "error_rate", "value": after, "baseline": before, "p_value": p},
            details={"boundary": boundary},
        )
    ]


def _lower_scores(scope: Scope, boundary: Scope, old: list[Row], new: list[Row], total: int) -> list[Finding]:
    a, b = [r["value"] for r in old], [r["value"] for r in new]
    before, after = mean(a), mean(b)
    p = mann_whitney_p(a, b)
    if before - after < SCORE_DROP or p >= ALPHA:
        return []
    return [
        finding(
            f"Scores of {where(scope)} fell from {before:.2f} to {after:.2f} (mean) after {_name(boundary)}",
            category="regression",
            scope=scope,
            affected=new,
            total=total,
            severity="high" if before - after >= 0.15 else "medium",
            metric={"name": "mean_score", "value": after, "baseline": before, "p_value": p},
            details={"boundary": boundary},
        )
    ]


def _name(boundary: Scope) -> str:
    names = {
        "prompt_version": "prompt v{} -> v{}",
        "model": "model {} -> {}",
        "code_version": "step version {} -> {}",
    }
    return names[boundary["field"]].format(boundary["from"], boundary["to"])
