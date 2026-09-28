"""Stage 5: rank the recorded inputs that separate a finding's affected runs from the other runs.

Every run in the finding's scope gets a set of features from its recorded inputs: prompt version, each
prompt section (id + version), model, sampling params and step params, step code version. For each feature
the effect is P(affected | feature) - P(affected | no feature) with a 95% confidence interval; features
present in every run (or none) cannot separate anything and are skipped. Near-equal effects prefer the
most specific kind (a section explains a prompt version). The LLM writes a hypothesis grounded in the top
causes and sample outputs; the cause stays `suspected` until a replay test (stage 6).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from hone_lens.budget import Budget
from hone_lens.findings import Cause, Finding, Fix
from hone_lens.llm import ask
from hone_lens.mapping import as_dict, as_list, json_value
from hone_lens.outputs.closeness import NO_SECTIONS
from hone_lens.ports import TextClient, TraceContext
from hone_lens.stats import difference_ci
from hone_lens.store import AnalyticsDB

KIND_ORDER = {"prompt_section": 0, "param": 1, "model": 2, "prompt_version": 3, "code_version": 4}
TOP = 5
MIN_RUNS = 5  # a feature needs this many runs with it and without it to be compared
NO_CAUSE = "no recorded input separates the affected runs from the others"
HYPOTHESIS = (
    "The user message describes a finding about an AI workflow, the recorded inputs that best separate the "
    "affected runs from the others (effect = difference in the affected share), how close the outputs are "
    "to each prompt section, and sample affected outputs. Write a short, grounded hypothesis (`hypothesis`) "
    "of why the finding happens. Do not claim more than the data shows."
)
Feature = tuple[str, str, str]  # kind, target, version ("" when the kind has none)


def explain(db: AnalyticsDB, finding: Finding, llm: TextClient | None, trace: TraceContext) -> Finding:
    """Rank causes for `finding`, set `cause` (the top one when it separates, else a `none` cause that says
    so), `fix` and `details["causes"]`."""
    runs = features_by_run(db, finding.scope)
    affected = db.run_ids(db.affected(finding.id) or finding.affected_ids) & set(runs)
    causes = rank_causes(runs, affected)
    finding.details["causes"] = causes[:TOP]
    if not any(kind == "prompt_section" for features in runs.values() for kind, _, _ in features):
        _add_note(finding, NO_SECTIONS)
    if not causes or causes[0]["ci"][0] <= 0:
        _add_note(finding, NO_CAUSE)
        finding.cause = Cause("none", "", hypothesis=NO_CAUSE)
        return finding
    top = causes[0]
    hypothesis = _hypothesis(db, finding, llm, trace)
    finding.cause = Cause(
        top["kind"], top["target"], effect_size=top["effect"], ci=top["ci"], hypothesis=hypothesis
    )
    finding.fix = _fix(top)
    return finding


def _add_note(finding: Finding, note: str) -> None:
    notes: list[str] = finding.details.setdefault("notes", [])
    if note not in notes:
        notes.append(note)


def scope_calls(db: AnalyticsDB, scope: Mapping[str, Any], columns: str) -> list[dict[str, Any]]:
    """The finding scope's recorded calls (its step, and its scorer or else the generator), oldest first."""
    return db.query(
        f"SELECT {columns} FROM calls WHERE workflow IS ?1 AND (?2 IS NULL OR step = ?2) "  # noqa: S608
        "AND scorer IS ?3 AND replay_of IS NULL ORDER BY start_time, span_id",
        (scope.get("workflow"), scope.get("step"), scope.get("scorer")),
    )


def features_by_run(db: AnalyticsDB, scope: dict[str, Any]) -> dict[str, set[Feature]]:
    """run id -> features of its calls (in the scope's step and scorer, or its generator calls) and steps."""
    calls = scope_calls(db, scope, "run_id, model, template_version, sections, temperature")
    runs: dict[str, set[Feature]] = {}
    for c in calls:
        features = runs.setdefault(c["run_id"], set())
        features |= {("model", str(c["model"]), ""), ("param", f"temperature={c['temperature']}", "")}
        if c["template_version"] is not None:
            features.add(("prompt_version", str(c["template_version"]), ""))
        for s in map(as_dict, as_list(json_value(c["sections"]))):
            features.add(("prompt_section", str(s.get("id")), str(s.get("version"))))
    steps = db.query(
        "SELECT run_id, step_version, params FROM steps WHERE workflow IS ?1 AND (?2 IS NULL OR step = ?2)",
        (scope.get("workflow"), scope.get("step")),
    )
    for st in steps:
        if st["run_id"] in runs:
            runs[st["run_id"]].add(("code_version", str(st["step_version"]), ""))
            params = as_dict(json_value(st["params"]))
            runs[st["run_id"]] |= {("param", f"{k}={json.dumps(v)}", "") for k, v in params.items()}
    return runs


def rank_causes(runs: dict[str, set[Feature]], affected: set[str]) -> list[dict[str, Any]]:
    """Every separating feature with its effect and 95% CI, strongest first."""
    causes: list[dict[str, Any]] = []
    for feature in sorted({f for features in runs.values() for f in features}):
        with_feature = {r for r, features in runs.items() if feature in features}
        without = set(runs) - with_feature
        if min(len(with_feature), len(without)) < MIN_RUNS:
            continue
        hits_with, hits_without = len(with_feature & affected), len(without & affected)
        kind, target, version = feature
        effect = hits_with / len(with_feature) - hits_without / len(without)
        causes.append(
            {
                "kind": kind,
                "target": target,
                "version": version,
                "effect": effect,
                "ci": difference_ci(hits_with, len(with_feature), hits_without, len(without)),
                "runs_with": len(with_feature),
                "affected_with": len(with_feature & affected),
            }
        )
    return sorted(causes, key=lambda c: (-round(c["effect"], 2), KIND_ORDER[c["kind"]], c["target"]))


def _hypothesis(db: AnalyticsDB, finding: Finding, llm: TextClient | None, trace: TraceContext) -> str:
    top = finding.details["causes"][0]
    version = f" v{top['version']}" if top["version"] else ""
    fallback = (
        f"Affected runs share {top['kind']} {top['target']}{version}: {top['affected_with']} of the "
        f"{top['runs_with']} runs with it are affected (effect {top['effect']:.2f})."
    )
    if llm is None:
        return fallback
    payload = {
        "finding": finding.title,
        "causes": finding.details["causes"],
        "closeness": finding.details.get("closeness", [])[:3],
        "samples": db.output_texts(finding.evidence[:5], max_chars=400),
    }
    answer, _ = ask(
        llm, "hypothesis", HYPOTHESIS, payload, {"hypothesis": {"type": "string"}}, Budget(), trace
    )
    return str(answer["hypothesis"]) if answer else fallback


def _fix(top: dict[str, Any]) -> Fix:
    kind, target, version = top["kind"], top["target"], top["version"]
    if kind == "prompt_section":
        return Fix(
            "prompt_diff",
            f"Remove or rewrite the {target!r} prompt section (v{version}); a replay test measures variants",
        )
    if kind == "model":
        return Fix("model_swap", f"Try another model instead of {target}")
    if kind == "param":
        return Fix("param", f"Change the parameter {target}")
    return Fix("prompt_diff", f"Compare {kind.replace('_', ' ')} {target} with the one before it")
