"""Review detector: `human_override` (people at gates, reject-and-revise cycles) and automated gate
reviewers (scripts behind the gate API, `hone.flow.gate.actor_kind = "automated"`)."""

from __future__ import annotations

from hone_lens.findings import Finding, rate_finding
from hone_lens.mapping import as_dict, as_list, json_value
from hone_lens.stats import Row, group_by
from hone_lens.store import AnalyticsDB

OVERRIDE_RATE = 0.2
AUTOMATED = "automated"  # `hone.flow.gate.actor_kind` of a scripted reviewer; anything else is a person
APPROVALS = frozenset({"approve", "approved"})
OVERRIDES = frozenset({"reject", "rejected", "edit", "edited", "overrule", "overruled"})


def human_override(db: AnalyticsDB) -> list[Finding]:
    """How often people reject, edit or overrule at each human gate (5 decisions at least), how often items
    of a step went through reject-and-revise cycles (hone-flow step records, `flow_steps`), and how many
    rounds items took to pass an automated reviewer."""
    rows = db.query(  # a gate span without a decision is the gate pausing, not a review
        "SELECT span_id, start_time, run_id, item, workflow, step, gate, decision, actor_kind "
        "FROM selections WHERE kind = 'human_gate' AND decision IS NOT NULL ORDER BY start_time, span_id"
    )
    people = [r for r in rows if r["actor_kind"] != AUTOMATED]  # no actor kind (older runs): a person
    findings: list[Finding] = []
    for (workflow, step, gate), group in group_by(people, "workflow", "step", "gate").items():
        overridden = [r for r in group if str(r["decision"] or "").lower() in OVERRIDES]
        findings += rate_finding(
            "gate decisions by people were rejections, edits or overrules",
            category="quality",
            scope={"workflow": workflow, "step": step, "gate": gate},
            hit=overridden if len(group) >= 5 else [],
            total=len(group),
            metric="override_rate",
            min_rate=OVERRIDE_RATE,
            high=0.4,
            medium=OVERRIDE_RATE,
        )
    return findings + _automated([r for r in rows if r["actor_kind"] == AUTOMATED]) + _revisions(db)


def _automated(rows: list[Row]) -> list[Finding]:
    """Rounds until approved at gates decided by a script: rejections before each item's approval."""
    findings: list[Finding] = []
    for (workflow, step, gate), group in group_by(rows, "workflow", "step", "gate").items():
        approvals: list[Row] = []
        rounds: list[int] = []
        for decisions in group_by(group, "run_id", "item").values():
            approved = [r for r in decisions if str(r["decision"]).lower() in APPROVALS]
            if approved:
                approvals.append(approved[-1])
                rounds.append(sum(str(r["decision"]).lower() in OVERRIDES for r in decisions))
        reworked = [a for a, n in zip(approvals, rounds, strict=True) if n]
        mean = sum(rounds) / len(rounds) if rounds else 0.0
        findings += rate_finding(
            f"items passed the automated review only after reject-and-revise rounds (mean {mean:.1f}, at "
            f"most {max(rounds, default=0)} rounds until approved)",
            category="quality",
            scope={"workflow": workflow, "step": step, "gate": gate},
            hit=reworked,
            total=len(approvals),
            metric="automated_rework_rate",
            min_rate=OVERRIDE_RATE,
            high=1.1,  # a check sending work back is the check working: low or medium only
            medium=0.5,
            details={
                "rounds_until_approved": {str(n): rounds.count(n) for n in sorted(set(rounds))},
                "mean_rounds": round(mean, 2),
                "max_rounds": max(rounds, default=0),
            },
        )
    return findings


def _revisions(db: AnalyticsDB) -> list[Finding]:
    """Items whose step output a reviewer rejected, so the step ran again (rejected attempts)."""
    # evidence: the step's latest span, or `run/step/item` when the run's spans were not ingested
    rows = db.query(
        "SELECT f.run_id, f.workflow, f.step, f.item, f.start_time, f.attempt_statuses, "
        "coalesce((SELECT s.span_id FROM steps s WHERE s.run_id = f.run_id AND s.step = f.step "
        "AND s.item IS f.item ORDER BY s.start_time DESC LIMIT 1), "
        "f.run_id || '/' || f.step || '/' || coalesce(f.item, '')) AS span_id "
        # steps that ran in this run (not copied by a fork, not pending / skipped / blocked before running)
        "FROM flow_steps f WHERE f.kind IS NOT 'gate' AND f.reused_from IS NULL AND f.attempt >= 1 "
        "ORDER BY f.start_time, f.run_id, f.step, f.item"
    )
    automated = _automated_only(db)  # sent back by a script (`_automated` measures those), not a person
    findings: list[Finding] = []
    for (workflow, step), group in group_by(rows, "workflow", "step").items():
        rejected = [
            0
            if (r["run_id"], r["item"]) in automated
            else as_list(json_value(r["attempt_statuses"])).count("rejected")
            for r in group
        ]
        revised = [r for r, n in zip(group, rejected, strict=True) if n]
        cycles = sum(rejected)
        findings += rate_finding(
            f"items were rejected at review and redone ({cycles} reject-and-revise cycles)",
            category="quality",
            scope={"workflow": workflow, "step": step},
            hit=revised,
            total=len(group),
            metric="revision_rate",
            min_rate=OVERRIDE_RATE,
            high=0.4,
            medium=OVERRIDE_RATE,
            details={"revisions": cycles, "max_revisions": max(rejected)},
        )
    return findings


def _automated_only(db: AnalyticsDB) -> set[tuple[str, str | None]]:
    """(run, item) pairs whose gate rejections all came from automated reviewers (`review_decisions` of
    every gate of the item)."""
    kinds: dict[tuple[str, str | None], set[str | None]] = {}
    for r in db.query("SELECT run_id, item, review_decisions FROM flow_steps WHERE kind = 'gate'"):
        decisions = [as_dict(d) for d in as_list(json_value(r["review_decisions"]))]
        rejections = {d.get("actor_kind") for d in decisions if str(d.get("decision")).lower() in OVERRIDES}
        kinds.setdefault((r["run_id"], r["item"]), set()).update(rejections)
    return {key for key, found in kinds.items() if found == {AUTOMATED}}
