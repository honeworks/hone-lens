"""The findings model (design/current.md §5): what is wrong, where, how much, why, and what fixes it.

Detectors build findings with `finding(...)`; the workspace gives them stable ids (`F-0001`) through their
content key and ranks them by impact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hone_lens.errors import HoneLensError

STATUSES = ("new", "confirmed_by_human", "dismissed", "fixed", "regressed")
SEVERITY_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}
# How much a category costs the user when it goes wrong (the "cost-or-quality weight" of current.md §5).
CATEGORY_WEIGHT = {"quality": 1.0, "diversity": 1.0, "reliability": 1.0, "regression": 1.0, "prompt": 0.9}
EVIDENCE_SAMPLE = 20


@dataclass
class Cause:
    """A suspected (or tested) cause: `kind` (`prompt_section`, `model`, `param`, ...) and its `target`."""

    kind: str
    target: str
    effect_size: float | None = None
    ci: tuple[float, float] | None = None
    status: str = "suspected"  # suspected | confirmed | refuted
    hypothesis: str = ""


@dataclass
class Fix:
    """A proposed change: `kind` is `prompt_diff`, `model_swap`, `param` or `new_gate`."""

    kind: str
    description: str
    diff: str = ""


@dataclass
class TestResult:
    """The measured effect of replaying affected inputs with a fix (stage 6)."""

    __test__ = False  # not a pytest test class

    confirmed: bool
    metric_name: str
    metric_before: float | None
    metric_after: float | None
    quality_before: float | None = None
    quality_after: float | None = None
    samples: int = 0
    variant: str = ""
    variants: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    cost_usd: float = 0.0
    calls: int = 0
    notes: list[str] = field(default_factory=list[str])


@dataclass
class Finding:
    """One finding. `id` is assigned by the workspace; detectors leave it empty."""

    title: str
    category: str = ""  # reliability | cost | quality | diversity | prompt | model | setup | regression
    severity: str = "medium"  # high | medium | low
    scope: dict[str, Any] = field(
        default_factory=dict[str, Any]
    )  # workflow, step, prompt_version, model, time_range
    affected: int = 0
    total: int = 0
    metric: dict[str, Any] = field(default_factory=dict[str, Any])  # name, value, baseline
    cause: Cause | None = None
    fix: Fix | None = None
    test: TestResult | None = None
    evidence: list[str] = field(default_factory=list[str])  # span ids, cluster ids
    status: str = "new"
    id: str = ""
    detector: str = ""
    details: dict[str, Any] = field(default_factory=dict[str, Any])
    # every affected span id (evidence keeps a sample); stored in its own table, loaded on demand
    affected_ids: list[str] = field(default_factory=list[str], repr=False, compare=False)

    @property
    def impact(self) -> float:
        """(affected / total) x severity weight x category weight (design/current.md §5)."""
        share = self.affected / self.total if self.total else 0.0
        return share * SEVERITY_WEIGHT.get(self.severity, 1.0) * CATEGORY_WEIGHT.get(self.category, 0.8)

    @property
    def key(self) -> str:
        """Content key: the same issue found again gets the same key (and so keeps its id and status)."""
        scope = {k: v for k, v in self.scope.items() if k != "time_range"}
        text = json.dumps([self.detector, self.category, scope, self.metric.get("name")], sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:24]


def finding(
    title: str,
    *,
    category: str,
    scope: Mapping[str, Any],
    affected: Iterable[Mapping[str, Any]],
    total: int,
    **fields: Any,
) -> Finding:
    """A finding from its affected rows (dicts with `span_id` and `start_time`): counts them, keeps a sample
    of evidence span ids and the time range. `fields` sets the other `Finding` fields (severity, metric)."""
    rows = sorted(affected, key=lambda r: (str(r["start_time"]), str(r["span_id"])))
    time_range = [rows[0]["start_time"], rows[-1]["start_time"]] if rows else None
    return Finding(
        title=title,
        category=category,
        scope={**{k: v for k, v in scope.items() if v is not None}, "time_range": time_range},
        affected=len(rows),
        total=total,
        evidence=sample_ids(rows),
        affected_ids=[str(r["span_id"]) for r in rows],
        **fields,
    )


def rate_finding(
    what: str,
    *,
    category: str,
    scope: Mapping[str, Any],
    hit: Sequence[Mapping[str, Any]],
    total: int,
    metric: str,
    min_rate: float,
    min_affected: int = 3,
    high: float = 0.2,
    medium: float = 0.05,
    **fields: Any,
) -> list[Finding]:
    """`[finding]` titled "<rate> of <where> <what>" when `hit` holds at least `min_affected` rows and a share
    of at least `min_rate` of `total`; `[]` otherwise. The metric's baseline is 0."""
    rate = len(hit) / total if total else 0.0
    if len(hit) < min_affected or rate < min_rate:
        return []
    return [
        finding(
            f"{pct(rate)} of {where(scope)} {what}",
            category=category,
            scope=scope,
            affected=hit,
            total=total,
            severity=severity_for(rate, high, medium),
            metric={"name": metric, "value": rate, "baseline": 0.0},
            **fields,
        )
    ]


def sample_ids(rows: Sequence[Mapping[str, Any]], n: int = EVIDENCE_SAMPLE) -> list[str]:
    """Up to `n` span ids spread evenly over `rows` (deterministic)."""
    if len(rows) <= n:
        return [str(r["span_id"]) for r in rows]
    return [str(rows[i * len(rows) // n]["span_id"]) for i in range(n)]


def severity_for(rate: float, high: float = 0.2, medium: float = 0.05) -> str:
    """`high` / `medium` / `low` for an affected share."""
    return "high" if rate >= high else "medium" if rate >= medium else "low"


def pct(rate: float) -> str:
    """A share as a short percentage for titles: 0.4 -> '40%', 0.004 -> '0.4%'."""
    value = rate * 100
    return f"{value:.0f}%" if value >= 1 or value == 0 else f"{value:.1f}%"


def where(scope: Mapping[str, Any]) -> str:
    """A short place name for titles: `song_ideas/ideas`, plus `model` / `scorer` when set."""
    place = "/".join(str(scope[k]) for k in ("workflow", "step") if scope.get(k)) or "all runs"
    extras = [f"{k} {scope[k]}" for k in ("scorer", "model", "prompt_version") if scope.get(k)]
    return f"{place} ({', '.join(extras)})" if extras else place


@dataclass
class Report:
    """What `Workspace.analyze` found: ranked findings (dismissed ones left out), notes about skipped or
    failed parts, and what the LLM stages spent."""

    workflow: str | None
    findings: list[Finding]
    notes: list[str] = field(default_factory=list[str])
    cost_usd: float = 0.0
    llm_calls: int = 0
    budget_exhausted: bool = False


def ranked(findings: Iterable[Finding]) -> list[Finding]:
    """Highest impact first; ties broken by content key so the order is deterministic."""
    return sorted(findings, key=lambda f: (-f.impact, f.key))


def carry_over(found: Iterable[Finding], known: Iterable[Finding]) -> list[Finding]:
    """`found`, ranked, each with the id, status, cause, fix and test of the stored finding with the same
    content key, or the next free id. A `fixed` finding found again in runs after its fix is `regressed`."""
    by_key = {f.key: f for f in known}
    next_number = 1 + max((int(f.id.removeprefix("F-")) for f in by_key.values()), default=0)
    findings = ranked(found)
    for f in findings:
        old = by_key.get(f.key)
        if old is None:
            f.id, next_number = f"F-{next_number:04d}", next_number + 1
            continue
        f.id, f.status = old.id, old.status
        f.cause, f.fix, f.test = f.cause or old.cause, f.fix or old.fix, f.test or old.test
        f.details = {**{k: v for k, v in old.details.items() if k in ("note", "fixed_at")}, **f.details}
        last_seen = (f.scope.get("time_range") or [None, None])[1]
        if old.status == "fixed" and last_seen and last_seen > str(old.details.get("fixed_at") or ""):
            f.status = "regressed"
    return findings


def shown(findings: Iterable[Finding], workflow: str | None = None) -> list[Finding]:
    """What reports show: not dismissed, of `workflow` (when given), most important first."""
    return [
        f
        for f in ranked(findings)
        if f.status != "dismissed" and (workflow is None or f.scope.get("workflow") == workflow)
    ]


def check_status(status: str | None) -> str | None:
    """`status` when it is None or one of `STATUSES`; raises `HoneLensError` otherwise."""
    if status is not None and status not in STATUSES:
        raise HoneLensError(f"unknown status {status!r}; choose from {list(STATUSES)}")
    return status
