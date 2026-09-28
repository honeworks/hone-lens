"""Cost and resource detectors: `cost_latency`, `gpu_thrash`."""

from __future__ import annotations

from itertools import pairwise
from statistics import median

from hone_lens.findings import Finding, finding, pct, where
from hone_lens.stats import Row, group_by
from hone_lens.store import AnalyticsDB

TOP_SHARE = 0.5  # with 2 steps a step must take 75% (1.5 / steps) to count as "most of the time"
TOKEN_GROWTH = 0.5
SLOW_LEASE_MS = 1000.0


def cost_latency(db: AnalyticsDB) -> list[Finding]:
    """Steps that take most of a workflow's time or cost, and prompt-token growth across prompt versions."""
    steps = db.query(
        "SELECT span_id, start_time, workflow, step, duration_ms FROM steps WHERE executed = 1 "
        "ORDER BY start_time, span_id"
    )
    calls = db.query(
        "SELECT span_id, start_time, workflow, step, scorer, template_version, input_tokens, cost_usd "
        "FROM calls WHERE replay_of IS NULL ORDER BY start_time, span_id"
    )
    return (
        _top_share(steps, "duration_ms", "time")
        + _top_share(calls, "cost_usd", "cost")
        + _token_growth(calls)
    )


def _top_share(rows: list[Row], column: str, what: str) -> list[Finding]:
    findings: list[Finding] = []
    for (workflow,), group in group_by(rows, "workflow").items():
        by_step = group_by([r for r in group if r[column]], "step")
        if len(by_step) < 2:
            continue
        totals = {step: sum(r[column] for r in step_rows) for (step,), step_rows in by_step.items()}
        step = max(totals, key=lambda s: totals[s])
        share = totals[step] / sum(totals.values())
        if share < max(TOP_SHARE, 1.5 / len(by_step)):
            continue
        findings.append(
            finding(
                f"Step {step} takes {pct(share)} of the {what} of {workflow}",
                category="cost",
                scope={"workflow": workflow, "step": step},
                affected=by_step[(step,)],
                total=len(group),
                severity="low",
                metric={"name": f"{what}_share", "value": share, "baseline": 1 / len(by_step)},
            )
        )
    return findings


def _token_growth(calls: list[Row]) -> list[Finding]:
    """Median prompt tokens of each prompt version against the version before (medians ignore the odd
    near-limit prompt)."""
    findings: list[Finding] = []
    versioned = [c for c in calls if c["template_version"] is not None and c["input_tokens"] is not None]
    for (workflow, step, scorer), group in group_by(versioned, "workflow", "step", "scorer").items():
        by_version = group_by(group, "template_version")  # first-seen order (rows are sorted by time)
        for ((before,), old), ((after,), new) in pairwise(by_version.items()):
            tokens_old = median(r["input_tokens"] for r in old)
            tokens_new = median(r["input_tokens"] for r in new)
            growth = tokens_new / tokens_old - 1 if tokens_old else 0.0
            if growth < TOKEN_GROWTH:
                continue
            scope = {"workflow": workflow, "step": step, "scorer": scorer, "prompt_version": after}
            findings.append(
                finding(
                    f"Prompt tokens of {where(scope)} grew {pct(growth)} from prompt v{before} to v{after}",
                    category="cost",
                    scope=scope,
                    affected=new,
                    total=len(group),
                    severity="medium" if growth >= 1 else "low",
                    metric={"name": "median_input_tokens", "value": tokens_new, "baseline": tokens_old},
                    details={"boundary": {"field": "prompt_version", "from": before, "to": after}},
                )
            )
    return findings


def gpu_thrash(db: AnalyticsDB) -> list[Finding]:
    """Model swaps per run, slow GPU leases, VRAM peaks and out-of-memory errors per workflow."""
    calls = db.query(
        "SELECT span_id, start_time, workflow, run_id, lease_wait_ms, vram_after_mb, gpu_unloaded, error "
        "FROM calls WHERE replay_of IS NULL ORDER BY start_time, span_id"
    )
    findings: list[Finding] = []
    for (workflow,), group in group_by(calls, "workflow").items():
        swaps_per_run = sum(bool(r["gpu_unloaded"]) for r in group) / len({r["run_id"] for r in group})
        waits = [r["lease_wait_ms"] for r in group if r["lease_wait_ms"] is not None]
        wait = median(waits) if waits else 0.0
        oom = sum(map(_out_of_memory, group))
        if swaps_per_run < 0.5 and wait < SLOW_LEASE_MS and not oom:
            continue
        peak = max((r["vram_after_mb"] for r in group if r["vram_after_mb"] is not None), default=None)
        findings.append(
            finding(
                f"GPU thrash in {workflow}: {swaps_per_run:.1f} model swaps per run, median lease wait "
                f"{wait:.0f} ms, {oom} out-of-memory errors",
                category="cost",
                scope={"workflow": workflow},
                affected=[r for r in group if _thrashed(r) or _out_of_memory(r)],
                total=len(group),
                severity="high" if oom or swaps_per_run >= 1 else "medium",
                metric={"name": "model_swaps_per_run", "value": swaps_per_run, "baseline": 0.0},
                details={"median_lease_wait_ms": wait, "oom_errors": oom, "vram_peak_mb": peak},
            )
        )
    return findings


def _thrashed(row: Row) -> bool:
    return bool(row["gpu_unloaded"]) or (row["lease_wait_ms"] or 0) >= SLOW_LEASE_MS


def _out_of_memory(row: Row) -> bool:
    return "out of memory" in (row["error"] or "").lower()
