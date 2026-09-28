"""Plain-text report for the terminal (no dependencies; the CLI prints it)."""

from __future__ import annotations

from hone_lens.findings import Finding, Report

_SCOPE_KEYS = ("step", "model", "scorer", "prompt_version", "gate")


def render(report: Report) -> str:
    """The report as text: one block per finding, most important first."""
    title = f"hone-lens: {report.workflow or 'all workflows'}"
    lines = [title, "=" * len(title), f"{len(report.findings)} findings"]
    for f in report.findings:
        lines += ["", *_finding(f)]
    if report.llm_calls or report.budget_exhausted:
        stopped = " (stopped at budget; results are partial)" if report.budget_exhausted else ""
        lines += ["", f"LLM cost: ${report.cost_usd:.4f} in {report.llm_calls} calls{stopped}"]
    if report.notes:
        lines += ["", "Notes:", *(f"  - {n}" for n in report.notes)]
    return "\n".join(lines) + "\n"


def _finding(f: Finding) -> list[str]:
    scope = " ".join(f"{k}={f.scope[k]}" for k in _SCOPE_KEYS if f.scope.get(k) is not None)
    lines = [
        f"{f.id}  [{f.severity}] {f.category}  {f.title}",
        f"        {f.affected}/{f.total} affected  {scope}  status={f.status}",
    ]
    if f.cause:
        lines.append(
            f"        cause ({f.cause.status}): {f.cause.kind} {f.cause.target}  {f.cause.hypothesis}"
        )
    if f.fix:
        lines.append(f"        fix: {f.fix.description}")
    if f.test:
        t = f.test
        verdict = "confirmed" if t.confirmed else "not confirmed"
        lines.append(f"        test ({verdict}): {t.metric_name} {t.metric_before} -> {t.metric_after}")
    if f.evidence:
        lines.append(f"        evidence: {', '.join(f.evidence[:5])}{' ...' if len(f.evidence) > 5 else ''}")
    return lines
