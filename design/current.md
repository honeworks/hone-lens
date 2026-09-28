# hone-lens: the design as it stands (v0.1.0)

This is the current design of hone-lens, written for people who want to understand, review or change it.
Why hone-lens exists is in [README.md](README.md); how it got here is in [changes/](changes/) and
[decisions.md](decisions.md). User documentation is in [`docs/`](../docs/concepts.md).

## 1. What it does

hone-lens reads everything AI workflows recorded, **across thousands of runs**, and produces
**findings**: what is going wrong, the suspected cause, a proposed fix and, when tested, the measured
effect of that fix, each with evidence. Most of the work is automated, partly with AI; a person confirms
the failure taxonomy, important findings and fixes. hone-lens **never changes a workflow on its own**.

It reads **standard OpenTelemetry GenAI traces from any tool** (OTLP JSON, Phoenix and Langfuse
exports) as well as honeworks span stores and hone-flow run folders, so it is useful to people who use no
other honeworks package. `hone.*` attributes (prompt sections, selection decisions, GPU leases, workflow
step statuses, reuse and fork provenance) unlock deeper findings when present.

The example finding it must be able to produce:

> **40% of `song_ideas` outputs share one pattern** (storm opening, named captain, three-line intro).
> **Cause:** the `format_example` section of prompt v3 (outputs unusually close to it; prompt v2 runs
> don't show the pattern). **Tested fix:** replacing it with 3 varied examples cut the largest cluster from
> 40% to 12%, quality unchanged (50 replayed inputs). **Evidence:** cluster summary, 20 sample traces.

### Rules the design keeps

- **Funnel:** statistics and embeddings cover every run; an LLM only reads samples. Labeling all runs
  with an LLM is opt-in and shows a cost estimate first.
- **Evidence for every claim**, linked to the spans and clusters behind it.
- **Causes stay suspected** until a replay test confirms them; a fix is tested on quality too, not only on
  the metric that triggered the finding.
- **Budgets and cost reports** for every LLM stage and replay test.
- **Explicit failure:** a model client that fails during analysis becomes a note in the report, never a
  crash and never a silent gap; "could not score" is `None`, never `0`.
- **Deterministic:** the same data and seeds give the same clusters, findings and ids.
- **Useful alone:** the core imports no other honeworks package and no optional extra.

## 2. Public API

```python
import hone_lens as tl

ws = tl.Workspace(path=".hone/lens", *, embedder=None, llm=None, replayer=None, step_rerunner=None)
ws.ingest(*sources: RecordSource | str) -> list[Ingested]
    # "hone:.hone", "hone:flows/song_video/runs", "flow:s3://bucket/prefix/song_video",
    # "otlp:traces/*.json", "phoenix:export.parquet", "langfuse:export.jsonl"
ws.analyze(workflow=None, *, since=None, stages=("stats", "outputs", "describe"), budget=None) -> Report
ws.review(workflow, *, io=None, sample=40, budget=None) -> Taxonomy            # stage 4 with a person
ws.label_all(workflow, taxonomy=None, *, budget, io=None, yes=False, force=False) -> LabelReport
ws.explain(finding_id, *, trace=None) -> Finding                              # stage 5
ws.test(finding_id, *, variants=3, samples=50, budget=None, quality=None, trace=None) -> TestResult
ws.findings(status=None) -> list[Finding];  ws.set_status(finding_id, status, note="")
ws.report(workflow=None, *, fmt="terminal" | "json" | "html", path=None) -> str

@tl.detector(category="quality", name=None)          # custom detectors over the analytics tables
def hooks_too_long(db: tl.AnalyticsDB) -> list[tl.Finding]: ...

tl.Finding, tl.Cause, tl.Fix, tl.TestResult, tl.Report, tl.Taxonomy, tl.LabelReport, tl.Budget
tl.sources: HoneSpanStore(path), OtlpJsonFiles(glob), PhoenixExport(path), LangfuseExport(path)
hone_lens.adapters.flow (extra `flow`): FlowRuns(storage, name), FlowStepRerunner(workflow)
hone_lens.adapters.openai (extra `openai`): OpenAITextClient(model), OpenAIEmbedder(model)
hone_lens.testing: synthetic_runs(...), FakeEmbedder, FakeTextClient, FakeReplayer, FakeStepRerunner,
                   FakeRecordSource, FakeFlowRuns, ScriptedIO, check_record_source, check_replayer,
                   check_text_client, check_embedder, check_step_rerunner
hone_lens.errors: HoneLensError, BudgetExceeded, SourceError
tl.PORTS_VERSION  # "1"
```

CLI (extra `cli`): `hone-lens ingest …`, `analyze <workflow> [--since 30d]`, `review <workflow>`,
`label <workflow> [--yes] [--force]`, `explain F-0042`, `test F-0042 --variants 3 --samples 50 --budget 2usd`,
`report <workflow> --html report.html | --json`, `findings [--status new]`, `set-status F-0042 dismissed`.
Models are named with `--llm` / `--embedder` / `--replayer NAME[:ARG]` and found through the entry-point
groups `hone.text_clients`, `hone.embedders` and `hone.replayers`. Exit codes: 0 success, 1 a hone-lens
error (message on stderr), 2 a usage error.

## 3. Usage example

This is also the main end-to-end test.

```python
import hone_lens as tl
from hone_lens.testing import synthetic_runs, FakeEmbedder, FakeTextClient, FakeReplayer

runs = synthetic_runs(tmp_path, n_runs=2000, plant=["homogeneity_from_example", "truncation",
                                                     "judge_equals_generator", "score_zero_spike"])
ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst(),
                  replayer=FakeReplayer.removing_section_reduces_similarity())
ws.ingest(f"hone:{runs.root}")
report = ws.analyze("song_ideas")
f = next(f for f in report.findings if f.category == "diversity")
f = ws.explain(f.id)
assert f.cause.kind == "prompt_section" and f.cause.target == "format_example"
t = ws.test(f.id, variants=2, samples=20)
assert t.confirmed and t.metric_after < t.metric_before
ws.report("song_ideas", fmt="html", path=tmp_path / "report.html")
```

## 4. The analysis funnel

| Stage | What | Needs | API |
|---|---|---|---|
| 0 ingest | read spans, derive the analytics tables | a source | `ws.ingest` |
| 1 stats | statistics detectors | nothing | `ws.analyze` |
| 2 outputs | embed every output, cluster, homogeneity, closeness to prompt sections | `embedder` | `ws.analyze` |
| 3 describe | the LLM describes each output cluster | `llm` | `ws.analyze` |
| 4 review | a person and the AI build a failure taxonomy; optional labeling of all outputs | `llm`, a person | `ws.review`, `ws.label_all` |
| 5 explain | rank the recorded inputs that separate affected from unaffected runs | nothing (`llm` adds a hypothesis) | `ws.explain` |
| 6 test | replay affected calls with variants of the cause; measure the effect | `replayer` (+ `embedder`) | `ws.test` |

A stage whose port is missing is skipped with a note (`analyze`) or raises `HoneLensError` saying what to
pass (`review`, `label_all`, `test`).

### Stage 0: ingest

- **Sources** implement the `RecordSource` port (§6) and yield spans (§7). Built in:

  | String | Class | Reads |
  |---|---|---|
  | `hone:<path>` | `HoneSpanStore(path)` | honeworks SQLite span stores (`spans.db`), JSONL span files, and hone-flow run folders: every `<run>/spans.jsonl` below a folder |
  | `otlp:<glob>` | `OtlpJsonFiles(glob)` | OTLP/JSON files from any OpenTelemetry instrumentation |
  | `phoenix:<file>` | `PhoenixExport(path, workflow=None)` | Phoenix span exports, Parquet (extra `phoenix`) or JSONL; OpenInference names mapped to GenAI names |
  | `langfuse:<file>` | `LangfuseExport(path, workflow=None)` | Langfuse observations (JSON array, API page or JSONL) |
  | `flow:<storage>/<workflow>` | `FlowRuns(storage, name)` (extra `flow`) | hone-flow runs through hone-flow's public read API, on any storage it supports, plus their step records |

- **Normalization.** A mapping layer turns every span into the records shape (§7): current `gen_ai.*`
  names (older ones and OpenInference / Langfuse fields are mapped), status as `ok` / `error` / `unset`,
  ISO-8601 UTC times. A store with a newer records schema version is refused with a message to upgrade.
- **Analytics tables** derived from the spans, rebuilt per trace for every trace an ingest touched (so a
  workflow name recorded by one package reaches the model calls recorded by another, whatever the ingest
  order):

  | Table | One row per |
  |---|---|
  | `calls` | model call: model, tokens, finish reason, prompt template / version / sections, structured-output path, latency, cost |
  | `steps` | workflow step: status, attempt, reuse / fork provenance, version, duration, system metrics |
  | `selections` | selection event: candidates, scores, gates, decisions, human gate decisions |
  | `outputs` | output text to analyze (generator calls; not judge calls, not replays) |
  | `run_calls` | workflow run, resume or fork call: run status, `fork_of`, warnings |
  | `flow_steps` | step record from a source that has them (`FlowRuns`): attempts and their statuses, review decisions, the run's seed |

  With plain OpenTelemetry traces the workflow is the resource `service.name`, the step is the parent
  span's name and the run is the trace.
- **Step status comes from the record, not the span status.** A `skipped`, `blocked`, `awaiting_review`,
  `interrupted` or fork-copied step is neither a failure nor a success; only steps that actually ran
  (`done` / `failed`, not copied) count in failure rates and durations.
- **Incremental.** Each source's position is stored per source name, in the same transaction as the
  spans: the change-log sequence (`changes.seq`) of SQLite stores, the byte offset of append-only
  `spans.jsonl` files (an unfinished last line waits for the next read), `[mtime, size]` of other files
  (re-read whole when changed), the newest `updated_at` for `FlowRuns`, and the latest start time for any
  other source. Spans are deduplicated by `span_id` (the first copy wins).
- **Step records.** A source may have an optional `step_records(since=)` method besides `spans`;
  `ws.ingest` calls it when present and replaces the `flow_steps` rows of every run it returns (a resumed
  run changes them). Review notes are never copied, only whether one was present.
- **Store.** One SQLite file, `<workspace>/lens.db` (standard library), through the small `AnalyticsDB`
  class. Its layout is not a stable interface; the API and the JSON report are. A workspace made by an
  older hone-lens gains the new columns when opened: the derived tables are rebuilt from the stored spans
  and the `flow:` sources' positions are cleared, so their step records are read again.

### Stage 1: statistics detectors (no LLM)

A detector is a plain function `(db: AnalyticsDB) -> list[Finding]`. `analyze(workflow, since=)` runs every
detector against the rows in scope (temporary tables shadow the analytics tables, so custom detectors get
the same slice with plain SQL). A detector that raises is reported in `Report.notes` and skipped.

| Detector | Signal |
|---|---|
| `failure_rate` | error, retry and structured-output repair rates per step, model, scorer and prompt version; failed step runs |
| `truncation` | `finish_reason=length`, prompt tokens near the context limit, repaired structured output |
| `cost_latency` | a step taking most of a workflow's time or cost; prompt-token growth across prompt versions |
| `gpu_thrash` | model swaps per run, slow GPU leases, out-of-memory errors |
| `reuse_health` | resumed runs that continued a step whose source changed without a version bump (`source_changed_version_unchanged` warnings; `stale_source_rate`) |
| `selection_health` | score spikes at 0 (possibly failed scores, or floor scores the judge explained, compared with the next level up on discrete scales, [0004](changes/0004-zero-scores-on-discrete-scales.md)), missing scores, gate rejections, fallbacks, thin winner margins, judge disagreement |
| `human_override` | people rejecting, editing or overruling at a gate; items people sent back for another attempt (`revision_rate`, from `flow_steps`); rounds until approved at gates decided by an automated reviewer (`hone.flow.gate.actor_kind = "automated"`, [0005](changes/0005-automated-gate-reviewers.md)) |
| `setup_smells` | a judge from the generator's model family; capability errors (e.g. HTTP 400 on images); the same prompt repeated with other random seeds in a run that records no seed (a hone-flow run's seed makes derived best-of-N seeds reproducible, [0003](changes/0003-seeded-best-of-n-is-not-unseeded.md)) |
| `regression` | latency, error rate or mean score changing significantly right after a prompt, model or step version change (Mann-Whitney / two-proportion test, `ALPHA = 0.01`) |

Thresholds, groupings and statistics are listed in [decisions.md](decisions.md#stage-1-detectors) and
[docs/detectors.md](../docs/detectors.md).

### Stage 2: output analysis

- Output texts are embedded through the `Embedder` port in batches, cached per (embedder model id, text
  hash).
- Outputs are clustered per (workflow, step) with one built-in, deterministic density clustering (numpy).
- Homogeneity measures: largest-cluster share, distinct-output rate, mean pairwise cosine, repeated 4-gram
  rate. A finding when the largest cluster holds >= 20% of a step's outputs or fewer than half are
  distinct, or when the largest-cluster share rose significantly across prompt versions.
- **Closeness to prompt sections:** each recorded prompt section (id + version) is embedded; the largest
  cluster's mean similarity to each section, compared with the other outputs, names candidate causes.
- Outliers: outputs far from everything else.
- An embedder failure ends stage 2 with a note; the stage 1 findings are kept.

### Stage 3: cluster descriptions

Clio / Kura style: one `TextClient` call per cluster with an evenly spread sample of its outputs returns a
description and "what these outputs have in common". Largest clusters first, within the budget;
descriptions are stored and not paid for again.

### Stage 4: human-in-the-loop taxonomy

`review()` draws a stratified sample (by cluster, detector evidence and score range); the AI writes a
one-line note per output, which the person accepts or edits; the AI proposes 5–8 failure modes from the
notes, which the person keeps, renames, drops or merges; the person labels the sampled outputs. It talks to
the person through a small `ReviewIO` (`ConsoleIO`, or `ScriptedIO` in tests) and refuses to run without a
terminal unless given an `io`.

`label_all()` (opt-in) first shows a token / USD estimate and needs confirmation (`yes=True`, CLI `--yes`);
it checks the LLM's labels against the person's on the sample and refuses below 70% agreement unless
forced; then it labels every output and returns counts.

### Stage 5: cause ranking

For a finding's affected runs versus the other runs in its scope, every recorded input is a feature:
prompt version, each prompt section id + version, model, temperature, step parameters, step code version.
Effect = P(affected | feature) − P(affected | no feature), with a 95% confidence interval (Newcombe). The
top feature is the suspected cause only when its interval excludes 0; otherwise the finding says no cause
was found. An LLM, when configured, adds a hypothesis grounded in the top causes and sample outputs, and a
proposed fix. Without recorded prompt sections the finding says a section-level cause is unavailable.

### Stage 6: replay test

A seeded sample of the calls that carry the suspected prompt section is replayed through the `Replayer`
port with variants of the section (removed, rewritten by the LLM, kept with a counter-instruction) and
everything else fixed. The finding's metric (largest-cluster share, truncation rate or error rate) is
recomputed on the replies, and quality with an optional `quality(text)` scorer. **Confirmed**: the best
variant lowers the metric significantly (p < 0.05) and quality drops by at most 0.05; **refuted**: no
variant lowers it; otherwise it stays **suspected**. The finding's own status never changes; a person
confirms it. Cost is reported. All LLM and replay calls of one `explain` or `test` share one trace
carrying `hone.lens.finding_id`.

## 5. Findings and reports

```python
@dataclass
class Finding:
    id: str                 # "F-0001", stable across re-analysis via a content key
    title: str
    category: str           # reliability | cost | quality | diversity | prompt | model | setup | regression
    severity: str           # high | medium | low
    scope: dict             # workflow, step, prompt version, model, time range
    affected: int
    total: int
    metric: dict            # name, value, baseline
    cause: Cause | None     # kind, target, effect_size, ci, status suspected|confirmed|refuted, hypothesis
    fix: Fix | None         # kind (prompt_diff | model_swap | param | new_gate), description, diff
    test: TestResult | None
    evidence: list[str]     # span ids, cluster ids
    status: str             # new | confirmed_by_human | dismissed | fixed | regressed
```

- Findings are ranked by impact = affected share × severity weight × category weight.
- **Stable ids.** A finding's content key is its detector, category, scope (without the time range) and
  metric name; a known key keeps its id, status, notes, cause, fix and test result.
- **Lifecycle.** hone-lens sets `new`; a person sets the rest. Dismissed findings stay dismissed and are
  left out of reports. A `fixed` finding seen again in runs that started after the fix was recorded becomes
  `regressed`.
- **Reports:** plain terminal text, JSON, and one self-contained HTML file (inline CSS, no scripts, no
  external assets) in which every evidence id links to a view of that trace or cluster.
- **Budgets:** `tl.Budget(usd=, tokens=, calls=, usd_per_1k_tokens=)`, or shorthands (`2.5`, `"2usd"`,
  `"5000 tokens"`, `"40 calls"`). The limit is checked before each call with an estimate; at the limit a
  stage stops, keeps what it finished and says so (`budget_exhausted`, `notes`, `cost_usd`, `llm_calls`).

## 6. Ports

hone-lens owns five small `typing.Protocol`s. Other packages (for example hone-models) implement them
structurally, without importing hone-lens. Payloads are plain JSON-compatible data or objects with the
listed attributes; unknown extra keys are ignored. `PORTS_VERSION = "1"`.

| Port | Shape | Used for |
|---|---|---|
| `RecordSource` | `name: str`; `spans(*, since=None) -> Iterable[span]` (ISO-8601 time filter); optional `step_records(*, since=None)` | where spans come from; `name` keys the ingest position |
| `TextClient` | `complete(messages, *, schema=None, trace=None, **params) -> TextResult` (`text`, `parsed`, `error`, `model`, `finish_reason`, `usage`, `span_id`) | the analysis LLM; model-quality problems come back as `error`, transport errors raise |
| `Embedder` | `model_id`, `dimensions`; `embed(texts, *, trace=None) -> list[list[float]]` (L2-normalized, order kept, `[]` for `[]`) | output analysis |
| `Replayer` | `replay_call(call_span, overrides, *, trace=None) -> span`; overrides `prompt.sections` (`{id: text or None}`), `model`, `params`, `messages` | replay tests; the returned span points back at the original |
| `StepRerunner` | `rerun(run_id, step, items, params) -> list[str]` (new run ids; the source run is never changed) | reserved for multi-step replay tests; accepted by `Workspace`, not yet called |

**Trace context.** Calls pass `trace: Mapping[str, str]` with a W3C `traceparent` and `hone.*` keys.
hone-lens starts one trace per `explain` / `test` call, with `hone.lens.finding_id`, and joins a caller's
trace when given one.

**Contract checkers.** `hone_lens.testing` exports `check_record_source`, `check_text_client`,
`check_embedder`, `check_replayer` and `check_step_rerunner`, so an implementation can prove it fits. The
fakes pass them.

**Adapters** live in `hone_lens.adapters`, one module per optional extra, imported lazily: `openai`
(`OpenAITextClient`, `OpenAIEmbedder` for any OpenAI-compatible server; entry points `openai` in
`hone.text_clients` and `hone.embedders`) and `flow` (`FlowRuns`, and `FlowStepRerunner(workflow)`, which
implements `StepRerunner` as a hone-flow fork).

## 7. Records

**What hone-lens reads.** Spans in the OpenTelemetry shape shared by the honeworks packages (records
schema version 1):

```json
{"trace_id": "<32 hex>", "span_id": "<16 hex>", "parent_span_id": "<16 hex> | null",
 "name": "hone.models.chat", "kind": "client",
 "start_time": "2026-09-27T14:03:11.120Z", "end_time": "2026-09-27T14:03:13.402Z",
 "status": {"code": "ok | error | unset", "message": ""},
 "attributes": {"gen_ai.request.model": "...", "hone.step": "...", "...": "..."},
 "events": [], "resource": {"service.name": "..."}, "links": []}
```

They arrive as SQLite span stores (a `spans` table plus a `changes` log for incremental readers), as JSONL
files (one span per line, e.g. a hone-flow run folder's `spans.jsonl`), as OTLP/JSON, or as Phoenix /
Langfuse exports. Standard `gen_ai.*` attributes are enough for statistics and output findings;
`hone.models.*` (prompt sections, structured-output path, context limit, cost, GPU leases, `replay_of`),
`hone.flow.*` (workflow, status, attempt, `reused_from`, `fork_of`, step version, params, gate decisions,
`warning` events) and `hone.select.*` (scores, gates, winners) attributes unlock the deeper findings. The
full list of attributes read is in [docs/records.md](../docs/records.md).

**What hone-lens writes.** No spans. Its analysis state (findings, clusters, embeddings, taxonomies,
labels, ingest positions) lives in the workspace database. Replays go through the `Replayer`, which records
them in its own stores with `hone.lens.finding_id` in the trace context; replayed calls are recognized when
ingested again and kept out of the analysis.

## 8. Test bed

`hone_lens.testing.synthetic_runs(root, n_runs, plant=[...], seed=0)` writes runs of a `song_ideas`
workflow as honeworks span stores (models and select as SQLite, hone-flow as run folders with
`spans.jsonl` and a minimal `manifest.json`) **and** as a plain OTLP JSON file without `hone.*`
attributes. Every fifth run is interrupted and resumed. Plantable issues, each with a known ground truth
(`runs.truth`):

| Plant | What it creates |
|---|---|
| `homogeneity_from_example` | prompt v3 adds a `format_example` section; about 40% of all outputs copy its structure; v2 runs don't |
| `truncation` | 5% of calls cut off at the length limit with prompts near the context limit, repaired JSON |
| `judge_equals_generator` | the judge is from the generator's model family |
| `score_zero_spike` | failed scores recorded as 0 |
| `vision_400` | capability errors |
| `gpu_thrash` | model swaps and out-of-memory errors |
| `source_changed_version_unchanged` | 15% of resumed runs carry hone-flow's stale-source warning |
| `nondeterministic_seed` | the same prompt repeated without a seed |
| `latency_regression_after_v4` | latency rises after prompt v4 |

Deterministic fakes for every port (`FakeEmbedder`, `FakeTextClient.analyst()`, `FakeReplayer`,
`FakeStepRerunner`, `FakeRecordSource`, `FakeFlowRuns`, `ScriptedIO`) make every stage testable offline.

## 9. Package layout

| Module | Job |
|---|---|
| `workspace.py` | the `Workspace` API |
| `sources/{hone,otlp,phoenix,langfuse}.py` | the built-in record sources |
| `mapping.py`, `derive.py`, `schema.py`, `store.py` | normalization, derived tables, the `AnalyticsDB` |
| `detectors/` | stage 1 and the `@detector` registry |
| `outputs/{embed,cluster,homogeneity,closeness}.py` | stage 2 |
| `describe.py`, `llm.py`, `budget.py` | stage 3, analysis prompts, budgets |
| `coding/{sample,review,label,taxonomy,io}.py` | stage 4 |
| `cause.py`, `replay.py`, `_tracing.py` | stages 5 and 6, the replay trace |
| `findings.py`, `report/{terminal,json,html}.py` | findings model, lifecycle, reports |
| `ports.py`, `adapters/`, `testing/` | ports, optional adapters, fakes and contract checkers |
| `cli.py` | the `hone-lens` command |

Core dependencies: `pydantic` and `numpy`. Extras: `cli` (typer, rich), `openai`, `phoenix` (pyarrow),
`flow` (hone-flow).

## 10. Guaranteed behaviour (acceptance cases)

Each case has an end-to-end test named `test_ac<N>_*` in `tests/e2e/` (AC-15 in `tests/gpu/`).

| AC | Scenario | Expected |
|---|---|---|
| AC-1 | §3 example (2,000 synthetic runs, fakes) | homogeneity finding with cause `prompt_section:format_example`; replay test confirms |
| AC-2 | Each planted issue alone | detected by the right detector with correct scope; no finding on a clean synthetic set (false-positive check) |
| AC-3 | Plain OTel GenAI traces (no `hone.*` fields) from OTLP JSON | ingest works; stats and output findings work; section-level cause unavailable, and the finding says so |
| AC-4 | Incremental ingest | a second ingest after adding 100 runs reads only new spans; results update |
| AC-5 | Regression detector | latency regression after prompt v4 flagged with the version boundary |
| AC-6 | Budgets | LLM stages stop at the budget with partial results and a cost report; `label_all` shows an estimate and needs confirmation (`--yes`) |
| AC-7 | Review with scripted IO | notes accepted / edited; taxonomy confirmed; agreement computed; labels counted |
| AC-8 | Dismiss and re-analyze | a dismissed finding stays dismissed; a fixed finding is re-checked |
| AC-9 | HTML report | one self-contained file; every finding links to evidence views; opens without network |
| AC-10 | Custom detector | registered and run; its findings appear in the report |
| AC-11 | Replay test refutes a wrong cause | the cause stays suspected or is refuted; never confirmed |
| AC-12 | Determinism | same data and seeds give the same clusters, findings and ids |
| AC-13 | Scale | 10,000 synthetic runs analyzed (stages 1–2, fake embedder) in under 60 s |
| AC-14 | Contract checkers | `check_record_source` and `check_replayer` pass on the sources and the fake replayer; exported |
| AC-15 **[real]** | Real embeddings (`HONE_TEST_EMBED_MODEL` via Ollama) and a real analysis LLM (`HONE_TEST_TEXT_MODEL`, OpenAI-compatible) on 300 synthetic runs | homogeneity cluster found; the cluster description mentions the planted pattern |
| AC-16 | hone-flow run folders via `hone:<folder>` | spans from every `spans.jsonl` ingested; step status from `hone.flow.status`, so `skipped`, `blocked`, `awaiting_review` steps are neither errors nor successes; `attempt`, `reused_from`, `fork_of` stored; a second ingest reads only new or grown files |
| AC-17 | `flow:` source with `FakeFlowRuns` | spans and step records; `flow_steps` rows with statuses, attempts, review decisions, `reused_from` / `fork_of`; a second ingest asks for runs updated since the last one and replaces a resumed run's rows; `hone_lens` imports without the `flow` extra; `flow:` without it raises `SourceError` naming `hone-lens[flow]` |
| AC-18 | `StepRerunner` via fork | `FlowStepRerunner(workflow).rerun(run_id, step, items, params)` calls `open_run(run_id).fork(refresh=(step,), items=items, params=params)` and returns `[new_run_id]`; `check_step_rerunner` passes |
| AC-19 | Workflow-run detectors | `reuse_health` finds the `source_changed_version_unchanged` plant with correct scope and nothing on a clean set; `human_override` counts reject-and-revise cycles from `flow_steps` |
| AC-20 | Examples | every `examples/*.py` runs offline, opens with a What / How / Why docstring and is listed in `examples/README.md` |
| AC-21 | Seeded best-of-N ([0003](changes/0003-seeded-best-of-n-is-not-unseeded.md)) | the same prompt with varied seeds inside hone-flow runs whose manifest has a seed gives no `unseeded_repeat_rate` finding; runs without a recorded seed still do |
| AC-22 | Floor scores ([0004](changes/0004-zero-scores-on-discrete-scales.md)) | a 1-5 judge whose zeros come with a reason and a confidence gives `floor_score_rate` with sample reasons, not `zero_score_rate`; zeros without them still give `zero_score_rate` |
| AC-23 | Automated gate reviewers ([0005](changes/0005-automated-gate-reviewers.md)) | decisions recorded with `actor_kind = "automated"` give `automated_rework_rate` (rounds until approved), never `override_rate` or `revision_rate`; decisions by a `person` or without an actor kind still do |

The README and every Python block in `docs/` are executed by the test suite as well.

## 11. Examples

[`examples/`](../examples/README.md) holds one runnable file per public concept, the reference to copy
from. Each opens with a docstring that says **what** it shows, **how** (the calls, in order) and **why**
(the problem it solves), runs top to bottom with fakes and synthetic data in a temporary folder (no
network, no GPU), prints a few lines and `assert`s the key facts. `examples/README.md` indexes them in
reading order, with the section of this page each one illustrates.

## 12. Known limits of v0.1

- USD budgets only stop LLM stages when the client reports `usage["cost_usd"]` or a price is given; the
  OpenAI adapter reports tokens only and the CLI has no price option, so from the CLI use a token or call
  budget.
- `hone-lens test` needs a `hone.replayers` entry point; hone-lens ships none (hone-models provides one).
- `phoenix:` / `langfuse:` source strings take the workflow name from the file name; use the classes for
  another name.
- Replay tests cover `prompt_section` causes only; item attributes and upstream outputs are not yet
  compared in cause ranking; hierarchical cluster descriptions are not built.
- Relative `since` durations (`"30d"`) count back from now.
