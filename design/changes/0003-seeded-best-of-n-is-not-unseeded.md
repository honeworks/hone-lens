# 0003: Seeded best-of-N generation is not an "unseeded repeat"

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28). Option 2 is built: the manifest's `seed`
(`flow:` source, `flow_steps.run_seed`) and `hone.flow.seed` on `hone.flow.run` / `hone.flow.step` spans
(`run_calls.seed`, `steps.seed`, read already so option 3 works as soon as hone-flow records it) mark a run
as seeded. Deferred to option 3's producer changes: checking that each varied call seed was a recorded,
derived one (needs `hone.select.seed`), and runs read through `hone:<folder>`, which carry no run seed
until hone-flow records `hone.flow.seed`.

## Context

Found while building the OneShotStudio demo app. Its `lyrics` step (a hone-flow step) runs a
hone-select selection: the generator sends the same lyrics prompt to `gemma4-12b` three times with the
variation seeds `ctx.seed`, `ctx.seed + 1`, `ctx.seed + 2`, where `ctx.seed` is hone-flow's stable seed
for the run, item and step. The run is fully reproducible: the same run seed gives the same three seeds.

`ws.analyze("oneshot", stages=("stats",))` over the first run reported:

```text
F-0001  [low] setup  100% of oneshot/lyrics (model gemma4-12b) calls repeat an identical prompt with
        another random seed, and the step has no seed parameter (runs are not reproducible)
```

The `_unseeded` detector (`detectors/setup.py`) treats a step as seeded only when its recorded params
contain a key named `seed`. hone-flow's step seed is not a param (it comes from `wf.run(..., seed=)` and
`ctx.seed`), and neither hone-flow's `hone.flow.step` span nor hone-select's `hone.select.generate` span
records the seed that was used, so the detector cannot see it.

## Problem

- Deliberate best-of-N sampling with derived, recorded-by-construction seeds is reported as a
  reproducibility smell in every workflow that uses hone-select inside hone-flow, the family's own
  recommended pattern. A finding that is always wrong for the recommended setup teaches people to ignore
  the setup category.
- The records that would settle it are missing: the seed a step or a generation used.

## Options

1. **Suppress inside selections**: skip calls whose ancestor is a `hone.select.generate` span. Cheap, but
   also hides a real unseeded generator inside a selection.
2. **Read the seeds that exist**: treat a step as seeded when its run has a seed (the `hone.flow.run`
   span or, through the `flow:` source, the manifest's `seed`), since `ctx.seed` derives from it.
3. **Ask the producers to record seeds**: `hone.flow.seed` on `hone.flow.step` spans (hone-flow) and
   `hone.select.seed` on `hone.select.generate` spans (hone-select); the detector then checks that each
   varied seed was a recorded, derived one. Needs a change record in each producer.

## Decision

Proposed: 2 now (only hone-lens changes), and 3 as follow-up proposals in hone-flow and hone-select; when
their attributes exist the detector prefers them. Option 1 is rejected because it would hide real
problems.

## Consequences

- No false `setup` finding for seeded best-of-N inside hone-flow runs.
- A generator that ignores the variation seed (calls with random seeds while the selection's seeds are
  recorded) becomes detectable once option 3 lands.
- Plain OpenTelemetry traces are unaffected (they have no run seed; the current rule still applies).

## Migration and compatibility

No API change. Existing workspaces keep old findings until the next `analyze`; findings that no longer
reproduce are marked the way any resolved finding is. The `steps` table may gain a `seed` column (derived
table, rebuilt on ingest).
