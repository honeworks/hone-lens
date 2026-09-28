"""Reliability detectors: `failure_rate`, `truncation`, `reuse_health`."""

from __future__ import annotations

from typing import Any

from hone_lens.findings import Finding, rate_finding
from hone_lens.mapping import as_dict, as_list, json_value
from hone_lens.stats import Row, group_by
from hone_lens.store import AnalyticsDB

MIN_RATE = 0.01
REPAIRED = ("retried", "repaired", "failed")
FAILED = ("failed", "error")  # hone.flow.status `failed`; span status `error` in other records
STALE_SOURCE = "source_changed_version_unchanged"


def failure_rate(db: AnalyticsDB) -> list[Finding]:
    """Error, retry and structured-output repair rates per step / model / scorer / prompt version, and step
    error rates."""
    calls = db.query(
        "SELECT span_id, start_time, workflow, step, model, scorer, template_version AS prompt_version, "
        "status, structured_path, retries FROM calls WHERE replay_of IS NULL ORDER BY start_time, span_id"
    )
    keys = ("workflow", "step", "model", "scorer", "prompt_version")
    findings: list[Finding] = []
    for values, group in group_by(calls, *keys).items():
        scope: dict[str, Any] = dict(zip(keys, values, strict=True))
        checks = (
            ("error_rate", "calls failed", [r for r in group if r["status"] == "error"]),
            ("retry_rate", "calls were retried", [r for r in group if r["retries"]]),
            (
                "repair_rate",
                "calls needed output repair",
                [r for r in group if r["structured_path"] in REPAIRED],
            ),
        )
        for metric, what, hit in checks:
            findings += _rate(what, scope, hit, len(group), metric)
    steps = db.query(  # skipped, blocked, awaiting_review and reused steps are neither errors nor successes
        "SELECT span_id, start_time, workflow, step, status FROM steps WHERE executed = 1 "
        "ORDER BY start_time, span_id"
    )
    for (workflow, step), group in group_by(steps, "workflow", "step").items():
        errors = [r for r in group if r["status"] in FAILED]
        findings += _rate(
            "step runs failed", {"workflow": workflow, "step": step}, errors, len(group), "step_error_rate"
        )
    return findings


def _rate(what: str, scope: dict[str, Any], hit: list[Row], total: int, metric: str) -> list[Finding]:
    return rate_finding(
        what, category="reliability", scope=scope, hit=hit, total=total, metric=metric, min_rate=MIN_RATE
    )


def truncation(db: AnalyticsDB) -> list[Finding]:
    """Calls cut off at the token limit (`finish_reason=length`), with how many had prompts near the context
    limit and how many needed structured-output repair."""
    calls = db.query(
        "SELECT span_id, start_time, workflow, step, model, finish_reason, input_tokens, context_limit, "
        "structured_path FROM calls WHERE replay_of IS NULL ORDER BY start_time, span_id"
    )
    findings: list[Finding] = []
    for (workflow, step, model), group in group_by(calls, "workflow", "step", "model").items():
        cut = [r for r in group if r["finish_reason"] == "length"]
        near = [r for r in cut if r["context_limit"] and (r["input_tokens"] or 0) >= 0.9 * r["context_limit"]]
        detail = f"; {len(near)} of them had prompts within 10% of the context limit" if near else ""
        findings += rate_finding(
            f"calls were cut off (finish_reason=length){detail}",
            category="reliability",
            scope={"workflow": workflow, "step": step, "model": model},
            hit=cut,
            total=len(group),
            metric="truncation_rate",
            min_rate=MIN_RATE,
            high=0.05,
            medium=0.01,
            details={
                "near_context_limit": len(near),
                "repaired": sum(r["structured_path"] in REPAIRED for r in cut),
            },
        )
    return findings


def reuse_health(db: AnalyticsDB) -> list[Finding]:
    """Resumed hone-flow runs that continued a step whose source changed without a version bump.

    hone-flow records a `warning` event (`kind: source_changed_version_unchanged`, `step`) on the
    `hone.flow.run` span of such a resume call. The rate is per step: runs with the warning / resumed runs.
    """
    calls = db.query(
        "SELECT span_id, start_time, run_id, workflow, warnings FROM run_calls ORDER BY start_time, span_id"
    )
    findings: list[Finding] = []
    for (workflow,), group in group_by(calls, "workflow").items():
        resumed = 0
        stale: dict[str, dict[str, Row]] = {}  # step -> run id -> the first call that warned
        for rows in group_by(group, "run_id").values():
            warned = [(w["step"], r) for r in rows for w in _warnings(r) if w.get("step")]
            resumed += int(len(rows) > 1 or bool(warned))
            for step, row in warned:
                stale.setdefault(step, {}).setdefault(row["run_id"], row)
        for step, hit in stale.items():
            findings += rate_finding(
                "resumed runs continued the step although its source changed without a version bump",
                category="reliability",
                scope={"workflow": workflow, "step": step},
                hit=list(hit.values()),
                total=resumed,
                metric="stale_source_rate",
                min_rate=0.0,
                min_affected=1,
                high=0.05,
                medium=0.01,
            )
    return findings


def _warnings(row: Row) -> list[dict[str, Any]]:
    found = [as_dict(w) for w in as_list(json_value(row["warnings"]))]
    return [w for w in found if w.get("kind") == STALE_SOURCE]
