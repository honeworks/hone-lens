# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Why the design changed is recorded in
[design/changes/](design/changes/).

## [0.1.0] - unreleased

The first release. Design: [0001 initial design](design/changes/0001-initial-design.md) and
[0002 workflow runs as run folders](design/changes/0002-run-folder-sources.md), made before the release.

### Changed (automated gate reviewers, [0005](design/changes/0005-automated-gate-reviewers.md))
- Gate decisions recorded with `hone.flow.gate.actor_kind = "automated"` (a script behind the gate API)
  are no longer counted as decisions by people (`override_rate`), and items only they sent back are not
  counted in `revision_rate`. They get their own measure, `automated_rework_rate`: items that passed only
  after reject-and-revise rounds, with the rounds until approved in `details` (low or medium severity).
  Decisions without an actor kind (older runs) are a person's, as before.
- New column `selections.actor_kind`; `flow_steps.review_decisions` keeps `actor_kind`.
- `human_override` moved to `hone_lens.detectors.review`.

### Changed (floor scores, [0004](design/changes/0004-zero-scores-on-discrete-scales.md))
- `selection_health` compares a spike of zeros with the next level up on a discrete scale (a 1-5 judge
  mapped to 0-1 has nothing in (0, 0.2)), and splits the zeros: the ones the judge answered (a reason, a
  confidence, no error) become the new `floor_score_rate` finding ("the judge rejects these outputs, or
  misreads the criterion") with sample reasons in `details`; only the others stay `zero_score_rate`
  ("failed scores recorded as 0?").
- New column `selections.reason` (`hone.select.score.reason`; `(not captured, N chars)` when hone-select
  recorded only its hash).

### Changed (seeded best-of-N, [0003](design/changes/0003-seeded-best-of-n-is-not-unseeded.md))
- `setup_smells` no longer reports `unseeded_repeat_rate` for repeated prompts with varied seeds inside a
  run that records a seed: the manifest's `seed` (new `flow_steps.run_seed`, read by `FlowRuns`) or
  `hone.flow.seed` on the run or step span (new `run_calls.seed` / `steps.seed`). Best-of-N with seeds
  derived from `ctx.seed` inside hone-flow is reproducible.
- A workspace made by an older hone-lens gains the new columns when opened: derived tables are rebuilt and
  the `flow:` sources re-read their step records on the next ingest.
- `FakeFlowRuns.add_run(..., seed=)` puts a run seed in the fake manifest.

### Changed (hone-flow run folders, [0002](design/changes/0002-run-folder-sources.md))
- hone-flow records are read from run folders: `hone:<folder>` reads every `<run>/spans.jsonl` below a
  folder, incrementally by byte offset (a grown file costs only its new lines; an unfinished last line
  waits for the next ingest).
- `steps` rows take `status` from `hone.flow.status` (`skipped`, `blocked`, `awaiting_review`, ... are
  neither failures nor successes), plus `attempt`, `reused_from`, `fork_of` and `executed`; the columns
  `cache_hit` / `cache_reason` are gone. New derived table `run_calls` (one row per run / resume / fork call).
- `reuse_health` replaces `cache_health`: resumed runs that continued a step whose source changed without
  a version bump (`stale_source_rate`). No detector reads `hone.flow.cache.*`.
- `human_override` ignores gate spans without a decision (a gate pausing) and counts reject-and-revise
  cycles per step from `flow_steps` (`revision_rate`).
- Synthetic runs write hone-flow run folders (`SyntheticRuns.flows`); every fifth run is resumed; the plant
  `cache_version_not_bumped` is now `source_changed_version_unchanged`.
- The HTML report prints evidence that is not a stored span as text instead of a dead link.
- `FakeStepRerunner` returns one new run id per call, as a fork does.

### Added (examples)
- 16 runnable examples, each opening with a What / How / Why docstring, indexed in reading order in
  `examples/README.md` and executed by the test suite (AC-20).

### Added (hone-flow run folders, [0002](design/changes/0002-run-folder-sources.md))
- `hone_lens.adapters.flow` (extra `flow` = `hone-flow>=0.1`): `FlowRuns(storage, name)` reads runs through
  hone-flow's read API (any storage, e.g. S3), incrementally via `runs(updated_since=...)`, and fills the
  new `flow_steps` table from step records; source string `flow:<storage>/<workflow>`.
  `FlowStepRerunner(workflow)` implements `StepRerunner` as a hone-flow fork.
- `hone_lens.testing.FakeFlowRuns` (hone-flow's read API shape, also a fake workflow for forks) and
  `check_step_rerunner`.

### Removed (cleanup)
- Unused `respx` dev dependency and the unused `comfyui` / `hosted` test markers; test-folder
  placeholders, unused test arguments; the
  AC-15 test embeds through urllib instead of httpx.

### Changed (working with the other honeworks packages)
- `cache_health` finds stale caches in the reason hone-flow actually records
  (`cache_hit; source_changed_version_unchanged`, reasons joined by `; `), not only an exact match.
- `testing.contracts.example_call_span()` carries `gen_ai.provider.name` (the shared records format), so a real
  replayer (hone-models) can tell which provider to call.

### Added ([0001](design/changes/0001-initial-design.md))
- `hone_lens.testing`: `synthetic_runs` planted-issue generator (9 plants, honeworks stores + OTLP JSON),
  `FakeEmbedder` (hash / `.semantic()`), `FakeTextClient` (scripted / `.analyst()`), `FakeReplayer`,
  `FakeStepRerunner`, `FakeRecordSource`, contract checkers `check_record_source`, `check_replayer`,
  `check_text_client`, `check_embedder`.
- Ports `RecordSource`, `TextClient`, `Embedder`, `Replayer`, `StepRerunner` (`PORTS_VERSION = "1"`).
- Sources `HoneSpanStore` (SQLite / JSONL span stores) and `OtlpJsonFiles` (OTLP JSON), mapping of older
  GenAI conventions, `Workspace.ingest` with per-source cursors and span-id dedupe, `AnalyticsDB` with
  derived `calls` / `steps` / `selections` / `outputs` tables.
- Stage 1: nine statistics detectors (`failure_rate`, `truncation`, `cost_latency`, `gpu_thrash`,
  `cache_health`, `selection_health`, `human_override`, `setup_smells`, `regression`), `@tl.detector` for
  custom ones, `Finding` / `Cause` / `Fix` / `TestResult` / `Report`, stable finding ids and statuses
  (`Workspace.analyze`, `findings`, `set_status`, `report`), plain-text terminal report.
- Stage 2: output embeddings (cached per model and text hash), deterministic built-in clustering,
  homogeneity (largest-cluster share, distinct rate, mean pairwise cosine, repeated 4-grams, regression
  across prompt versions), outliers, closeness of the largest cluster to each recorded prompt section.
- Stage 3: LLM cluster descriptions (`description`, `in_common`) within a `tl.Budget`; reports carry the
  LLM cost, call count and whether the budget stopped a stage.
- Stage 4: `Workspace.review` (AI notes on a stratified sample, confirmed by a person; AI-proposed failure
  modes the person keeps / renames / drops / merges; human labels), `Workspace.label_all` (cost estimate,
  confirmation, agreement check, label counts), `ConsoleIO`, `testing.ScriptedIO`.
- Stage 5: `Workspace.explain` ranks recorded inputs (prompt version, sections, model, params, code
  version) by effect with confidence intervals, sets a suspected cause, an LLM hypothesis and a fix.
- Stage 6: `Workspace.test` replays variants of a prompt-section cause through the `Replayer` port and
  confirms, refutes or keeps the cause suspected, with cost and an optional quality check.
- Findings lifecycle: dismissed findings stay dismissed, fixed findings seen again in later runs become
  `regressed`; `ws.report(fmt="json" | "html")` with a self-contained HTML file linking every finding to
  its evidence traces and clusters.
- Sources `PhoenixExport` (Parquet / JSONL, OpenInference names mapped) and `LangfuseExport` (JSON /
  JSONL observations); source strings `phoenix:` and `langfuse:`.
- `hone-lens` CLI (`cli` extra) and `hone_lens.adapters.openai` (`OpenAITextClient`, `OpenAIEmbedder`,
  `openai` extra, entry points `hone.text_clients:openai`, `hone.embedders:openai`).
- Documentation (README, `docs/`), runnable `examples/`, all executed by the test suite; real-model
  acceptance test (AC-15) in the `gpu` suite.
