"""Selection detector: `selection_health` (scores, gates, decisions, judges)."""

from __future__ import annotations

import re
from typing import Any

from hone_lens.findings import Finding, rate_finding
from hone_lens.mapping import as_dict, as_list, json_value
from hone_lens.stats import Row, group_by
from hone_lens.store import AnalyticsDB

ZERO_SPIKE = 0.05
NEAR_ZERO = 0.2  # continuous scores: "just above 0"
DISCRETE_LEVELS = 11  # fewer distinct non-zero values than this (and >= 2 scores a level): a discrete scale
FLOOR_HIGH = 0.3
REASON_CHARS = 200
# a zero whose reason reads like an error message is not the judge's answer
ERROR_LIKE = re.compile(
    r"^\W*(error|exception|failed|could not|couldn't|unable to)\b|traceback|timed out|invalid json|"
    r"(parse|parsing) (error|failed)",
    re.IGNORECASE,
)
NONE_RATE = 0.05
GATE_REJECTION = 0.5
FALLBACK_RATE = 0.1
LOW_MARGIN, LOW_MARGIN_RATE = 0.02, 0.3
DISAGREEMENT, DISAGREEMENT_RATE = 0.5, 0.2


def selection_health(db: AnalyticsDB) -> list[Finding]:
    """Scores spiking at 0, scores missing (None), gate rejections, fallback use, thin winner margins and
    judges that disagree."""
    rows = db.query(
        "SELECT span_id, start_time, workflow, kind, candidate_id, scorer, value, confidence, reason, error, "
        "gate, passed, fallback_used, ranked FROM selections ORDER BY start_time, span_id"
    )
    scores = [r for r in rows if r["kind"] == "score"]
    findings: list[Finding] = []
    for (workflow, scorer), group in group_by(scores, "workflow", "scorer").items():
        scope = {"workflow": workflow, "scorer": scorer}
        missing = [r for r in group if r["value"] is None]
        findings += _zero_spike(db, scope, group)
        findings += _rate("scores are missing (None)", scope, missing, group, "none_score_rate", NONE_RATE)
    gates = [r for r in rows if r["kind"] == "gate"]
    for (workflow, gate), group in group_by(gates, "workflow", "gate").items():
        rejected = [r for r in group if r["passed"] is not None and not r["passed"]]
        scope = {"workflow": workflow, "gate": gate}
        findings += _rate(
            "candidates were rejected", scope, rejected, group, "gate_rejection_rate", GATE_REJECTION
        )
    decisions = [r for r in rows if r["kind"] == "decision"]
    for (workflow,), group in group_by(decisions, "workflow").items():
        scope = {"workflow": workflow}
        fallbacks = [r for r in group if r["fallback_used"]]
        thin = [r for r in group if (_margin(r["ranked"]) or 1.0) < LOW_MARGIN]
        findings += _rate("selections fell back", scope, fallbacks, group, "fallback_rate", FALLBACK_RATE)
        findings += _rate("winners won by < 0.02", scope, thin, group, "low_margin_rate", LOW_MARGIN_RATE)
    return findings + _disagreement(scores)


def _rate(what: str, scope: dict[str, Any], hit: list[Row], group: list[Row], metric: str, min_rate: float):
    category = "reliability" if metric == "none_score_rate" else "quality"
    return rate_finding(
        what,
        category=category,
        scope=scope,
        hit=hit,
        total=len(group),
        metric=metric,
        min_rate=min_rate,
        high=0.5,
        medium=min_rate,
    )


def _zero_spike(db: AnalyticsDB, scope: dict[str, Any], group: list[Row]) -> list[Finding]:
    """Many exact zeros but few scores at the next level up. Zeros the judge answered (a reason and a
    confidence, no error) are floor scores; the others are possibly failures recorded as 0."""
    zeros = [r for r in group if r["value"] == 0]
    if not zeros or len(_next_level(group)) >= len(zeros) / 2:
        return []
    answered = [r for r in zeros if _answered(r)]
    failed = [r for r in zeros if not _answered(r)]
    return _failed_zeros(db, scope, failed, len(group)) + _floor_scores(scope, answered, len(group))


def _next_level(group: list[Row]) -> list[float]:
    """The scores just above 0: on a discrete scale (few distinct values, e.g. 1-5 mapped to 0, 0.25, ...,
    1) the lowest non-zero level; otherwise the scores in (0, 0.2)."""
    values = [r["value"] for r in group if r["value"] is not None]
    levels = sorted({v for v in values if v > 0})
    if levels and len(levels) < DISCRETE_LEVELS and len(values) >= 2 * (len(levels) + 1):
        return [v for v in values if v == levels[0]]
    return [v for v in values if 0 < v < NEAR_ZERO]


def _answered(row: Row) -> bool:
    reason = row["reason"] or ""
    return (
        bool(reason) and row["confidence"] is not None and not row["error"] and not ERROR_LIKE.search(reason)
    )


def _failed_zeros(db: AnalyticsDB, scope: dict[str, Any], zeros: list[Row], total: int) -> list[Finding]:
    if not zeros:
        return []
    failed = db.query(
        "SELECT count(*) AS n FROM calls WHERE workflow IS ? AND scorer = ? AND structured_path = 'failed'",
        (scope["workflow"], scope["scorer"]),
    )[0]["n"]
    hint = f"; {failed} judge calls of this scorer failed to parse" if failed else ""
    return rate_finding(
        f"scores are exactly 0 (failed scores recorded as 0?){hint}",
        category="quality",
        scope=scope,
        hit=zeros,
        total=total,
        metric="zero_score_rate",
        min_rate=ZERO_SPIKE,
        high=0.1,
        medium=ZERO_SPIKE,
        details={"failed_judge_calls": failed},
    )


def _floor_scores(scope: dict[str, Any], zeros: list[Row], total: int) -> list[Finding]:
    """Zeros the judge explained: it rejects these outputs, or misreads the criterion."""
    return rate_finding(
        "scores are the lowest possible (0), with the judge's reasons (the judge rejects these outputs, or "
        "misreads the criterion)",
        category="quality",
        scope=scope,
        hit=zeros,
        total=total,
        metric="floor_score_rate",
        min_rate=ZERO_SPIKE,
        high=FLOOR_HIGH,
        medium=ZERO_SPIKE * 2,
        details={"sample_reasons": [str(r["reason"])[:REASON_CHARS] for r in zeros[:3]]},
    )


def _margin(ranked: str | None) -> float | None:
    """Winner's total minus the runner-up's, from `hone.select.ranked` ([[id, total]] or [{id, total}])."""
    totals = sorted((t for t in map(_total, as_list(json_value(ranked))) if t is not None), reverse=True)
    return totals[0] - totals[1] if len(totals) >= 2 else None


def _total(item: Any) -> float | None:
    if isinstance(item, dict):
        value = as_dict(item).get("total")
    else:
        pair = as_list(item)
        value = pair[1] if len(pair) > 1 else None
    return float(value) if isinstance(value, int | float) else None


def _disagreement(scores: list[Row]) -> list[Finding]:
    """Candidates scored by several judges whose scores are more than 0.5 apart."""
    findings: list[Finding] = []
    for (workflow,), group in group_by(scores, "workflow").items():
        scored = [r for r in group if r["value"] is not None and r["candidate_id"]]
        multi = [
            rows for rows in group_by(scored, "candidate_id").values() if len({r["scorer"] for r in rows}) > 1
        ]
        split = [rows[0] for rows in multi if _spread(rows) > DISAGREEMENT]
        findings += _rate(
            "candidates got scores more than 0.5 apart from different judges",
            {"workflow": workflow},
            split,
            [rows[0] for rows in multi],
            "judge_disagreement_rate",
            DISAGREEMENT_RATE,
        )
    return findings


def _spread(rows: list[Row]) -> float:
    values = [r["value"] for r in rows]
    return max(values) - min(values)
