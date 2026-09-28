# 0005: Tell automated gate reviewers from people

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28), the hone-lens side of option 1: hone-lens reads
`hone.flow.gate.actor_kind` (gate spans) and `actor_kind` in hone-flow's review records, and reports
automated gates as "rounds until approved" (`automated_rework_rate`). Takes effect once hone-flow records
the attribute (its own change record: `run.reject(..., automated=True)`); until then every decision is a
person's, as before. Deferred: "share approved with remaining failures", which needs a record of the
failures left at approval that hone-flow does not write.

## Context

Found in the concept-shorts demo app. Its `render_check` gate is decided by a script, not a
person: scenes whose code crashes or breaks the layout are rejected with the errors as the note
(hone-flow reject-and-revise), then approved after at most N rounds. Every decision is recorded with
`actor = "render-check"`. `concept-shorts analyze` reports:

> F-0005 [high] quality: 50% of concept_shorts/render_check gate decisions **by people** were rejections,
> edits or overrules

and F-0001 ("100% of code items were rejected at review and redone"). For an automated check, a high
rejection rate in the first round is the design working (the check found crashes and sent them back),
not a sign that people disagree with the model. The finding reads as a human-review problem and ranks
as `high`.

## Problem

hone-lens assumes every gate decision is a person's. Apps increasingly put scripted reviewers
(render checks, validators, CI-like gates) behind the same gate API, because reject-and-revise with a
note is exactly what they need.

## Options

1. **An actor kind in the record.** hone-flow records `hone.flow.gate.actor_kind` (`person` or
   `automated`, from `run.reject(..., actor=..., automated=True)`); hone-lens reports the two
   separately: for automated gates, "rounds until approved" and "share approved with remaining
   failures" instead of "decisions by people".
2. **A naming convention** (actors ending in `-check` or starting with `bot:` are automated) configured
   in hone-lens. No change in hone-flow, but fragile.
3. **Leave it**; apps ignore the finding.

## Decision

Proposed: option 1 (needs a matching small change in hone-flow's gate record).

## Consequences

- Human-review findings stay meaningful; automated gates get their own, more useful measures.
- Old runs without the attribute are treated as `person`, as today.
