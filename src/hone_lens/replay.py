"""Stage 6: counterfactual replay test of a finding's suspected prompt-section cause.

A seeded sample of the calls that have the section is replayed through the `Replayer` port with each
variant of the section (removed, rewritten by the LLM, or followed by a counter-instruction), everything
else fixed. The finding's metric is measured on the original and the replayed outputs of that sample (so
`metric_before` is the sample's value, not the whole finding's), and quality too when a quality scorer is
given. The cause is confirmed when the best variant improves the metric beyond noise (two-proportion
test) without losing more than `QUALITY_TOLERANCE` of quality, refuted when no variant improves it at all,
and stays suspected otherwise.
"""

from __future__ import annotations

import difflib
import random
from collections.abc import Callable
from statistics import mean
from typing import Any

from hone_lens.budget import Budget
from hone_lens.cause import scope_calls
from hone_lens.errors import BudgetExceeded, HoneLensError
from hone_lens.findings import Finding, Fix, TestResult
from hone_lens.llm import MAX_OUTPUT_TOKENS, ask
from hone_lens.mapping import as_dict, as_list, finish_reason, json_value, output_text
from hone_lens.outputs.closeness import section_texts
from hone_lens.outputs.cluster import SIMILARITY
from hone_lens.outputs.embed import embed
from hone_lens.ports import Embedder, Replayer, TextClient, TraceContext
from hone_lens.stats import two_proportion_p
from hone_lens.store import AnalyticsDB

ALPHA = 0.05
QUALITY_TOLERANCE = 0.05
SEED = 0
COUNTER = (
    "Do not copy the structure, names or images of any example above; make this output clearly different."
)
REWRITE = (
    "The user message holds a prompt section that the finding suggests causes a problem. Write `n` "
    "alternative versions of the section (`variants`) that keep its purpose but avoid the problem."
)
Quality = Callable[[str], float | None]
Span = dict[str, Any]


def replay_test(
    db: AnalyticsDB,
    finding: Finding,
    replayer: Replayer,
    *,
    embedder: Embedder | None,
    llm: TextClient | None,
    quality: Quality | None,
    variants: int,
    samples: int,
    budget: Budget,
    trace: TraceContext,
) -> TestResult:
    """Replay the cause's variants on `samples` calls; returns the `TestResult` (the finding is updated)."""
    cause, name = finding.cause, str(finding.metric.get("name"))
    if cause is None or cause.kind != "prompt_section":
        return TestResult(False, name, None, None, notes=["replay tests need a prompt_section cause in v0.1"])
    if finding.metric.get("name") == "largest_cluster_share" and embedder is None:
        raise HoneLensError(
            "testing a diversity finding needs an embedder: Workspace(embedder=...) or --embedder"
        )
    measure = _measure(db, finding, embedder)
    if measure is None:
        return TestResult(False, name, None, None, notes=[f"no replay measure for metric {name!r} in v0.1"])
    calls = _sample_calls(db, finding, cause.target, samples)
    section = _section_text(db, calls, cause.target)
    mark = budget.mark()
    result = TestResult(False, name, measure(calls), None, samples=len(calls))
    result.quality_before = _quality(quality, calls)
    for label, text in _variants(finding, section, variants, llm, budget, trace, result.notes):
        replayed, stopped = _replay(replayer, calls, {cause.target: text}, budget, trace, result.notes)
        if replayed:
            result.variants.append(
                {
                    "variant": label,
                    "section": text,
                    "metric_after": measure(replayed),
                    "quality_after": _quality(quality, replayed),
                    "replayed": len(replayed),
                }
            )
        if stopped:
            break
    result.cost_usd, result.calls = budget.since(mark)
    _judge(finding, result, section)
    return result


def _measure(
    db: AnalyticsDB, finding: Finding, embedder: Embedder | None
) -> Callable[[list[Span]], float] | None:
    """The finding's metric as a function of call spans (their outputs); None if it cannot be replayed."""
    name = finding.metric.get("name")
    if name == "truncation_rate":
        return lambda spans: mean(finish_reason(s["attributes"]) == "length" for s in spans)
    if name == "error_rate":
        return lambda spans: mean(s["status"]["code"] == "error" for s in spans)
    cluster_id = finding.details.get("cluster_id")
    if name != "largest_cluster_share" or embedder is None or not cluster_id:
        return None
    (center,) = db.query("SELECT center FROM clusters WHERE id = ?", (cluster_id,))
    centre = embed(db, embedder, db.output_texts([center["center"]], max_chars=10**9))[0]

    def share_near_centre(spans: list[Span]) -> float:
        similarities = (embed(db, embedder, [output_text(s) for s in spans]) @ centre).tolist()
        return sum(sim >= SIMILARITY for sim in similarities) / len(similarities)

    return share_near_centre


def _sample_calls(db: AnalyticsDB, finding: Finding, section: str, samples: int) -> list[Span]:
    """A seeded sample of the scope's calls whose prompt has the section (the calls a fix would change)."""
    rows = scope_calls(db, finding.scope, "span_id, sections")
    ids = [
        r["span_id"]
        for r in rows
        if section in {as_dict(s).get("id") for s in as_list(json_value(r["sections"]))}
    ]
    return db.spans(random.Random(SEED).sample(ids, min(samples, len(ids))))


def _section_text(db: AnalyticsDB, calls: list[Span], section: str) -> str:
    texts = section_texts(db, [c["span_id"] for c in calls])
    return next((text for (section_id, _), text in texts.items() if section_id == section), "")


def _variants(
    finding: Finding,
    section: str,
    n: int,
    llm: TextClient | None,
    budget: Budget,
    trace: TraceContext,
    notes: list[str],
) -> list[tuple[str, str | None]]:
    """Remove the section, the LLM's rewrites, and the section plus a counter-instruction: the first `n`."""
    rewrites: list[str] = []
    if n > 2 and llm is None:
        notes.append(f"no LLM to rewrite the section: {min(n, 2)} of {n} variants")
    elif n > 2 and llm is not None:
        payload = {"finding": finding.title, "section": section, "n": n - 2}
        try:
            answer, error = ask(
                llm, "section_variants", REWRITE, payload, {"variants": {"type": "array"}}, budget, trace
            )
        except BudgetExceeded as e:
            answer, error = None, str(e)
        rewrites = [str(v) for v in as_list((answer or {}).get("variants"))][: n - 2]
        if len(rewrites) < n - 2:
            notes.append(f"the LLM wrote {len(rewrites)} of {n - 2} section rewrites ({error or 'too few'})")
    options: list[tuple[str, str | None]] = [("remove", None)]
    options += [(f"rewrite {i}", text) for i, text in enumerate(rewrites, 1)]
    options.append(("counter-instruction", f"{section}\n{COUNTER}"))
    return options[:n]


def _replay(
    replayer: Replayer,
    calls: list[Span],
    sections: dict[str, str | None],
    budget: Budget,
    trace: TraceContext,
    notes: list[str],
) -> tuple[list[Span], bool]:
    """The replayed calls, and whether the budget stopped the replays (what was paid for is kept)."""
    replayed: list[Span] = []
    for span in calls:
        try:
            budget.check(int(span["attributes"].get("gen_ai.usage.input_tokens") or 0) + MAX_OUTPUT_TOKENS)
        except BudgetExceeded as e:
            notes.append(
                f"replay stopped: {e}; {len(replayed)} of {len(calls)} calls of this variant replayed"
            )
            return replayed, True
        try:
            new = dict(replayer.replay_call(span, {"prompt.sections": sections}, trace=trace))
        except Exception as e:  # current.md §6: record a failed replay and go on
            budget.charge({})
            notes.append(f"replay of {span['span_id']} failed: {type(e).__name__}: {e}")
            continue
        a = new.get("attributes", {})
        usage = {
            "input_tokens": a.get("gen_ai.usage.input_tokens"),
            "output_tokens": a.get("gen_ai.usage.output_tokens"),
        }
        budget.charge({**usage, "cost_usd": a.get("hone.models.cost_usd")})
        replayed.append({**new, "status": new.get("status") or {"code": "ok", "message": ""}})
    return replayed, False


def _quality(quality: Quality | None, spans: list[Span]) -> float | None:
    if quality is None or not spans:
        return None
    scores = [q for q in (quality(output_text(s)) for s in spans) if q is not None]
    return mean(scores) if scores else None


def _judge(finding: Finding, result: TestResult, section: str) -> None:
    """Pick the best variant; confirm, refute or keep the cause suspected; set or clear the fix."""
    if finding.cause is None or not result.variants or result.metric_before is None:
        result.notes.append("nothing was replayed; the cause stays suspected")
        return
    best = min(result.variants, key=lambda v: v["metric_after"])
    before, after = result.metric_before, best["metric_after"]
    result.variant, result.metric_after, result.quality_after = best["variant"], after, best["quality_after"]
    hits_before, hits_after = round(before * result.samples), round(after * best["replayed"])
    improved = (
        after < before and two_proportion_p(hits_before, result.samples, hits_after, best["replayed"]) < ALPHA
    )
    if result.quality_before is None or result.quality_after is None:
        result.notes.append(
            "quality not measured: pass quality=... to guard against a fix that hurts quality"
        )
        kept_quality = True
    else:
        kept_quality = result.quality_after >= result.quality_before - QUALITY_TOLERANCE
    result.confirmed = improved and kept_quality
    if result.confirmed:
        finding.cause.status = "confirmed"
        old, new = section.splitlines(), (best["section"] or "").splitlines()
        diff = "\n".join(difflib.unified_diff(old, new, "before", "after", lineterm=""))
        finding.fix = Fix(
            "prompt_diff", f"{best['variant']} the {finding.cause.target!r} prompt section", diff
        )
    elif all(v["metric_after"] >= before for v in result.variants):
        finding.cause.status = "refuted"
        finding.fix = None
    else:
        finding.cause.status = "suspected"
