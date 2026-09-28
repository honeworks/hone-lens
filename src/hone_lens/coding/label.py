"""`label_all`: the LLM labels every output of a workflow with the confirmed taxonomy, after a cost
estimate, a confirmation and an agreement check against the person's labels of the reviewed sample."""

from __future__ import annotations

from collections import Counter
from typing import Any

from hone_lens.budget import Budget
from hone_lens.coding.io import ReviewIO
from hone_lens.coding.review import suggest_mode
from hone_lens.coding.taxonomy import LabelReport, Taxonomy
from hone_lens.errors import BudgetExceeded
from hone_lens.findings import pct
from hone_lens.llm import MAX_OUTPUT_TOKENS
from hone_lens.ports import TextClient
from hone_lens.store import AnalyticsDB

MIN_AGREEMENT = 0.7
_PROMPT_TOKENS = 150  # instruction + schema + the modes, per call


def label_all(
    db: AnalyticsDB,
    llm: TextClient,
    io: ReviewIO,
    taxonomy: Taxonomy,
    budget: Budget,
    *,
    yes: bool = False,
    force: bool = False,
) -> LabelReport:
    outputs = db.query(
        "SELECT span_id, text FROM outputs WHERE workflow IS ? ORDER BY start_time, span_id",
        (taxonomy.workflow,),
    )
    tokens = sum(len(o["text"]) // 4 for o in outputs) + len(outputs) * (_PROMPT_TOKENS + MAX_OUTPUT_TOKENS)
    report = LabelReport(taxonomy.workflow, len(outputs), tokens, tokens * budget.usd_per_1k_tokens / 1000)
    price = f"${report.estimate_usd:.2f}" if budget.usd_per_1k_tokens else "no price set"
    io.show(
        f"label_all: {len(outputs)} outputs of {taxonomy.workflow}, one LLM call each, about {tokens} tokens "
        f"({price})"
    )
    if not yes and io.ask("Label them? [y/N]").lower() not in ("y", "yes"):
        report.refused = "not confirmed; answer 'y' or pass yes=True (CLI: --yes)"
        return report
    mark = budget.mark()
    try:
        _check_agreement(llm, taxonomy, budget, report, force)
        if not report.refused:
            reviewed = {n.span_id for n in taxonomy.notes}
            _label(db, llm, taxonomy, [o for o in outputs if o["span_id"] not in reviewed], budget, report)
    except BudgetExceeded as e:
        report.budget_exhausted = True
        report.notes.append(
            f"label_all stopped: {e}; {report.outputs - report.labeled} outputs left unlabeled"
        )
    report.cost_usd, report.llm_calls = budget.since(mark)
    return report


def _check_agreement(
    llm: TextClient, taxonomy: Taxonomy, budget: Budget, report: LabelReport, force: bool
) -> None:
    """Share of the reviewed outputs where the LLM's label equals the person's (None counts as a label)."""
    if not taxonomy.notes:
        report.refused = "the taxonomy has no reviewed sample to check agreement on; run review() first"
        return
    same = sum(
        suggest_mode(llm, taxonomy.modes, {"output": n.output}, budget) == n.label for n in taxonomy.notes
    )
    report.agreement = same / len(taxonomy.notes)
    if report.agreement < MIN_AGREEMENT and not force:
        report.refused = (
            f"the LLM agrees with the reviewed labels on only {pct(report.agreement)} "
            f"(< {pct(MIN_AGREEMENT)}); improve the taxonomy or pass force=True"
        )


def _label(
    db: AnalyticsDB,
    llm: TextClient,
    taxonomy: Taxonomy,
    todo: list[dict[str, Any]],
    budget: Budget,
    report: LabelReport,
) -> None:
    labels = {n.span_id: (n.label, "human") for n in taxonomy.notes}
    report.labeled = len(labels)
    try:
        for o in todo:
            labels[o["span_id"]] = (suggest_mode(llm, taxonomy.modes, {"output": o["text"]}, budget), "llm")
            report.labeled += 1
    finally:
        db.save_labels(taxonomy.workflow, labels)
        report.counts = dict(Counter(label or "(none)" for label, _ in labels.values()).most_common())
