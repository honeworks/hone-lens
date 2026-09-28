# 0002: Workflow runs as run folders

## Status

`implemented in 0.1.0` (made before the first release, on top of [0001](0001-initial-design.md)).

## Context

hone-lens reads the records of workflow runners to find step-level problems: failed and retried steps,
slow steps, stale reuse, human reviews. The first design (0001) read hone-flow, the honeworks workflow
runner, the way it read the other honeworks packages: from a SQLite span store (`.hone/flow/spans.db`)
next to hone-flow's metadata tables, with step reuse recorded as a cross-run cache
(`hone.flow.cache.hit`, `hone.flow.cache.reason`). A `cache_health` detector watched cache hit rates and
"source changed but version didn't" cache reasons, and the synthetic test bed had a matching
`cache_version_not_bumped` plant.

While hone-lens v0.1 was being finished, hone-flow was redesigned around **run folders**: every run is a
self-contained folder (`<storage>/<workflow>/runs/<run_id>/`, local or on S3) that holds the run's outputs,
its spans in `spans.jsonl` and its step records in documented JSON (`manifest.json`, `metadata.json`).
There is no shared SQLite store, no metadata tables and no cross-run cache: a run can be **resumed**, and
reuse happens only through an explicit **fork** of a run, recorded as `hone.flow.reused_from` /
`hone.flow.fork_of`. Step spans now carry a status of their own (`hone.flow.status`: `done`, `failed`,
`skipped`, `blocked`, `awaiting_review`, `interrupted`, ...) and an attempt number. hone-flow also gained a
public read API (`open_runs(storage, name)`, `runs(updated_since=)`, `open_run(id).spans()` / `.steps()`).

A test of all honeworks packages together had also shown a gap in the first design: hone-lens took a
step's status from the span status code only, so skipped or blocked steps looked like successes.

## Problem

- hone-lens had no reader for run folders, so it could not see any workflow run recorded by the
  redesigned runner.
- `cache_health` and the `steps.cache_hit` / `cache_reason` columns described a mechanism that no longer
  exists.
- Step records with more detail than spans (earlier attempts and why they ended, review decisions) were
  out of reach, and so were runs stored on S3.
- The `StepRerunner` port (re-run a step for some items, for multi-step replay tests) had no
  implementation; with run folders the natural one is a fork.

## Options

| Question | Options |
|---|---|
| How to read run folders | (a) extend the core `hone:` source to read every `spans.jsonl` below a local folder; (b) parse `manifest.json` / `metadata.json` directly in the core; (c) read through hone-flow's public read API, in an optional extra; (d) both (a) and (c) |
| Where step status comes from | the span status code (as before); `hone.flow.status` when present |
| What replaces `cache_health` | drop it; a detector over the `source_changed_version_unchanged` warning events hone-flow puts on resumed runs |
| How `StepRerunner` is implemented | a dedicated "rerun step" call; a fork of the run that refreshes the step for the chosen items |

## Decision

(d): two ways in, producing the same rows.

- **`hone:<folder>` (core)** reads every `spans.jsonl` below a local folder, incrementally by byte offset:
  the files are append-only, so a grown file costs only its new lines, a shorter file is read again, and
  an unfinished last line waits for the next ingest. No hone-flow install is needed.
- **`flow:<storage>/<workflow>` (extra `flow`)** is `FlowRuns(storage, name)` in
  `hone_lens.adapters.flow`, which reads through hone-flow's read API, so it works on S3 and any storage
  hone-flow supports. Besides spans it reads **step records** into a new `flow_steps` table (one row per
  run, step and item, with status, attempts and their statuses, review decisions, `reused_from`,
  `fork_of`, the error). Review notes are not copied, only whether one was present. The read API is used
  instead of parsing the JSON files in the core, so hone-lens depends on a documented interface, and
  the core still never imports hone-flow (only the adapter module does, lazily). The `RecordSource` port
  is unchanged: `step_records(since=)` is an optional method that `ws.ingest` calls when a source has it,
  and a resumed run's rows are replaced when it is read again.
- **Step status from the record.** `steps.status` comes from `hone.flow.status` (the span status code
  only when that is absent), with `attempt`, `reused_from`, `fork_of` and an `executed` flag. Only steps
  that actually ran count in failure rates and durations; skipped, blocked, paused and fork-copied steps
  are neither failures nor successes. A new derived table, `run_calls`, has one row per run, resume or
  fork call with its status, `fork_of` and warnings.
- **`reuse_health` replaces `cache_health`.** It finds resumed runs that continued a step whose source
  changed without a version bump (`stale_source_rate`, from hone-flow's `warning` events). No detector
  reads `hone.flow.cache.*`.
- **`human_override`** ignores gate spans without a decision (a gate pausing, not a review) and counts
  reject-and-revise cycles per step from `flow_steps` (`revision_rate`).
- **`FlowStepRerunner(workflow)`** implements `StepRerunner` as a fork:
  `[workflow.open_run(run_id).fork(refresh=(step,), items=items, params=params).run_id]`. A fork leaves
  the source run untouched and copies everything the refresh does not need to recompute. `Workspace`
  accepts it; replay tests do not use it yet.
- **Test bed.** Synthetic runs write run folders; every fifth run is interrupted and resumed; the
  `cache_version_not_bumped` plant became `source_changed_version_unchanged` (the warning on 15% of resumed
  runs). `FakeFlowRuns` has the read API's shape (and a `fork()`), and `check_step_rerunner` joined the
  contract checkers.
- **Development dependency.** Until hone-flow is published, the `flow` extra's `hone-flow>=0.1` is
  resolved from a sibling checkout by a uv path source (`[tool.uv.sources]` in `pyproject.toml`); the wheel
  itself only declares `hone-flow>=0.1`. Removing the path source before publishing is **awaiting owner
  review**.

## Consequences

- Workflow runs are analyzed wherever they are stored, with or without hone-flow installed.
- Step-level findings no longer mistake skipped, blocked or waiting steps for successes, and review
  cycles become visible.
- The `flow:` source depends on hone-flow's read API fields (`RunSummary.updated_at` / `fork_of`,
  `StepRecord.attempts[].status`, `reviews[].decision`); a change there needs a change here, checked by
  hone-lens' tests against the real hone-flow.
- hone-flow's `runs(updated_since=)` is inclusive, so the newest run is re-read on every ingest (spans are
  deduplicated and rows replaced, so this costs time, not correctness).
- Within `analyze(since=)`, a resume whose first call is older than the window counts as resumed only if
  it warned; the stale-source rate can therefore rise slightly in a narrow window.

## Migration and compatibility

- `cache_health` is removed; use `reuse_health`. Its findings, and the `steps.cache_hit` /
  `steps.cache_reason` columns, are gone.
- The synthetic plant `cache_version_not_bumped` is renamed `source_changed_version_unchanged`.
- `FakeStepRerunner` returns one new run id per call, as a fork does.
- No released version had the old behaviour. A workspace created by a pre-release build has the old
  `steps` columns: delete its `lens.db` and ingest again.
- Records of the old hone-flow design (`.hone/flow/spans.db` with `hone.flow.cache.*`) are still read as
  ordinary span stores, but no detector uses the cache attributes.
