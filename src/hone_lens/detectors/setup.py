"""`setup_smells`: judge from the generator's model family, capability errors, unseeded randomness."""

from __future__ import annotations

import re

from hone_lens.findings import Finding, finding, rate_finding
from hone_lens.mapping import as_dict, json_value
from hone_lens.stats import Row, group_by
from hone_lens.store import AnalyticsDB

# runs that record a seed: on the `hone.flow.run` span, on a step span, or in the manifest (`flow:` source)
SEEDED_RUNS = (
    "SELECT run_id FROM run_calls WHERE seed IS NOT NULL "
    "UNION SELECT run_id FROM steps WHERE seed IS NOT NULL "
    "UNION SELECT run_id FROM flow_steps WHERE run_seed IS NOT NULL"
)
CAPABILITY_ERROR = re.compile(r"\b400\b|not support|unsupported|capabilit", re.IGNORECASE)


def family(model: str) -> str:
    """Model family: the name up to the first `-` or `:`, e.g. `ollama/gemma4-12b:latest` -> `gemma4`."""
    return re.split(r"[-:]", model.rsplit("/", 1)[-1].lower(), maxsplit=1)[0]


def setup_smells(db: AnalyticsDB) -> list[Finding]:
    calls = db.query(
        "SELECT span_id, start_time, run_id, workflow, step, model, scorer, status, error, seed, input_sha "
        "FROM calls WHERE replay_of IS NULL ORDER BY start_time, span_id"
    )
    return _same_family(calls) + _capability_errors(calls) + _unseeded(db, calls)


def _same_family(calls: list[Row]) -> list[Finding]:
    findings: list[Finding] = []
    for (workflow,), group in group_by(calls, "workflow").items():
        generators = {family(r["model"]) for r in group if r["model"] and not r["scorer"]}
        judged = [r for r in group if r["model"] and r["scorer"]]
        for (scorer, model), judge_calls in group_by(judged, "scorer", "model").items():
            if family(model) in generators:
                findings.append(
                    finding(
                        f"Judge {scorer} in {workflow} uses {model}, the same model family as the generator "
                        "(self-preference bias)",
                        category="setup",
                        scope={"workflow": workflow, "scorer": scorer, "model": model},
                        affected=judge_calls,
                        total=len(judge_calls),
                        severity="medium",
                        metric={"name": "same_family_judge_share", "value": 1.0, "baseline": 0.0},
                    )
                )
    return findings


def _capability_errors(calls: list[Row]) -> list[Finding]:
    findings: list[Finding] = []
    for (workflow, scorer, model), group in group_by(calls, "workflow", "scorer", "model").items():
        failed = [r for r in group if r["status"] == "error" and CAPABILITY_ERROR.search(r["error"] or "")]
        findings += rate_finding(
            f"calls failed with a capability error: {failed[0]['error']}" if failed else "",
            category="setup",
            scope={"workflow": workflow, "scorer": scorer, "model": model},
            hit=failed,
            total=len(group),
            metric="capability_error_rate",
            min_rate=0.0,
            high=0.0,
        )
    return findings


def _unseeded(db: AnalyticsDB, calls: list[Row]) -> list[Finding]:
    """Identical prompts sent with different random seeds, in a step without a seed parameter whose run
    records no seed (a hone-flow run's seed makes derived variation seeds, as in best-of-N, reproducible)."""
    seeded_steps = {
        (r["workflow"], r["step"])
        for r in db.query("SELECT DISTINCT workflow, step, params FROM steps WHERE params IS NOT NULL")
        if "seed" in as_dict(json_value(r["params"]))
    }
    seeded_runs = {r["run_id"] for r in db.query(SEEDED_RUNS)}
    findings: list[Finding] = []
    for (workflow, step, model), group in group_by(calls, "workflow", "step", "model").items():
        unseeded = [
            r for r in group if r["seed"] is not None and r["input_sha"] and r["run_id"] not in seeded_runs
        ]
        same_input = group_by(unseeded, "input_sha")
        varied = [r for rows in same_input.values() if len({r["seed"] for r in rows}) > 1 for r in rows]
        findings += rate_finding(
            "calls repeat an identical prompt with another random seed, and neither the step (no seed "
            "parameter) nor its run records a seed (runs are not reproducible)",
            category="setup",
            scope={"workflow": workflow, "step": step, "model": model},
            hit=[] if (workflow, step) in seeded_steps else varied,
            total=len(group),
            metric="unseeded_repeat_rate",
            min_rate=0.0,
            high=1.1,
            medium=1.1,  # always low: a reproducibility smell, not a failure
        )
    return findings
