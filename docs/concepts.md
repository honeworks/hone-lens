# Concepts

hone-lens answers one question about an AI workflow that has run many times: *what goes wrong, why, and
does a fix help?* This page explains the pieces. Every code block on this page runs as written (the docs
are executed by the test suite), in an empty folder.

## Workspace

A `Workspace` is a folder that holds everything hone-lens knows about your runs: the ingested spans,
the analytics tables derived from them, embeddings, clusters, findings and their statuses. The data lives
in one SQLite file, `<folder>/lens.db`. Open the same folder again and everything is still there.

```python
import hone_lens as tl

ws = tl.Workspace("lens")  # creates lens/lens.db; the default path is ".hone/lens"
print(ws.findings())  # [] until something is analyzed
```

A workspace takes up to four optional **ports**, the objects it uses to talk to models:

| Argument | Port | Needed by |
|---|---|---|
| `embedder=` | `Embedder` | output analysis (stage 2) and replay tests of diversity findings |
| `llm=` | `TextClient` | cluster descriptions, review, labeling, cause hypotheses, rewritten fix variants |
| `replayer=` | `Replayer` | replay tests (stage 6) |
| `step_rerunner=` | `StepRerunner` | reserved for multi-step replay (v2); `hone_lens.adapters.flow.FlowStepRerunner(workflow)` implements it as a hone-flow `fork` of the run; accepted but unused in v0.1 |

A stage whose port is missing is skipped with a note (`analyze`) or raises `HoneLensError` with a message
that says what to pass (`review`, `label_all`, `test`). [adapters.md](adapters.md) shows how to write
your own ports; `hone_lens.testing` has deterministic fakes for all of them.

## Sources

A source is where spans come from. `ws.ingest(...)` takes source strings or source objects:

| String | Class | Reads |
|---|---|---|
| `"hone:<path>"` | `HoneSpanStore(path)` | honeworks span stores (`spans.db`, `spans.jsonl`), a file or every store below a folder |
| `"otlp:<glob>"` | `OtlpJsonFiles(glob)` | OTLP/JSON trace files from any OpenTelemetry instrumentation |
| `"phoenix:<file>"` | `PhoenixExport(path, workflow=None)` | Arize Phoenix span exports (Parquet or JSONL) |
| `"langfuse:<file>"` | `LangfuseExport(path, workflow=None)` | Langfuse observation exports (JSON or JSONL) |
| `"flow:<storage>/<workflow>"` | `hone_lens.adapters.flow.FlowRuns(storage, name)` | hone-flow runs through hone-flow's read API (any storage, e.g. S3), with step records; extra `flow` |

Ingest is incremental: each source's position is remembered, a second ingest reads only what is new, and
spans already stored (same `span_id`) are skipped. Details and examples: [sources.md](sources.md).

Ingested spans are normalized and turned into **analytics tables** that every stage reads:

| Table | One row per |
|---|---|
| `calls` | model call: model, tokens, finish reason, prompt version and sections, structured-output path, latency, cost |
| `steps` | workflow step (hone-flow): status (`done`, `failed`, `skipped`, `blocked`, `awaiting_review`, ...), attempt, reuse / fork provenance, version, duration |
| `selections` | selection event: scores, gates, decisions, human gate decisions |
| `outputs` | model output text to analyze (generator calls; not judge calls, not replays) |
| `run_calls` | hone-flow run, resume or fork call: run status, `fork_of`, warnings |
| `flow_steps` | hone-flow step record (from the `flow:` source): attempts and their statuses, review decisions |

Every row has `workflow`, `step` and `run_id`. With honeworks records these come from hone-flow. With
plain OpenTelemetry traces the workflow is the resource `service.name`, the step is the parent span's name,
and the run is the trace.

## The six stages

| Stage | What | Needs | API |
|---|---|---|---|
| 0 ingest | read spans, derive the analytics tables | a source | `ws.ingest` |
| 1 stats | nine statistics detectors (failures, truncation, cost, regressions, ...) | nothing | `ws.analyze(stages=("stats",))` |
| 2 outputs | embed every output, cluster per step, find repeated patterns and what they copy | `embedder` | `ws.analyze` (stage `"outputs"`) |
| 3 describe | the LLM describes each output cluster in words | `llm` | `ws.analyze` (stage `"describe"`) |
| 4 review | a person and the AI build a failure taxonomy; optional labeling of all outputs | `llm`, a person | `ws.review`, `ws.label_all` |
| 5 explain | rank the recorded inputs that separate affected runs from the others | nothing (`llm` adds a hypothesis) | `ws.explain` |
| 6 test | replay affected calls with variants of the cause, measure the effect | `replayer` (and `embedder` for diversity findings) | `ws.test` |

`analyze` runs stages 1 to 3 (`stages=("stats", "outputs", "describe")` by default). Stages 4 to 6 run
per workflow or per finding, when you ask. [detectors.md](detectors.md) covers stage 1,
[analysis.md](analysis.md) the rest.

## Findings

A `Finding` is one issue with its evidence:

| Field | Meaning |
|---|---|
| `id` | `F-0001`, `F-0002`, ... stable across analyses |
| `title` | one sentence with the numbers, e.g. "3% of song_ideas/ideas (model gemma4-12b) calls were cut off" |
| `category` | `reliability`, `cost`, `quality`, `diversity`, `setup`, `regression` (custom detectors may use `prompt`, `model`) |
| `severity` | `high`, `medium`, `low` (from the affected share) |
| `scope` | where: `workflow`, `step`, and when relevant `model`, `scorer`, `prompt_version`, `gate`, plus `time_range` |
| `affected`, `total` | how many rows are affected out of how many in scope |
| `metric` | `{"name", "value", "baseline"}` (regressions add `p_value`) |
| `cause` | `Cause(kind, target, effect_size, ci, status, hypothesis)` after `explain` |
| `fix` | `Fix(kind, description, diff)`: `prompt_diff`, `model_swap`, `param` or `new_gate` |
| `test` | `TestResult` after `test` |
| `evidence` | up to 20 span ids spread over the affected rows; cluster ids (`C-...`) for output findings |
| `status` | `new`, `confirmed_by_human`, `dismissed`, `fixed`, `regressed` |
| `detector` | the detector that produced it |
| `details` | detector-specific extras (cluster description, closeness to prompt sections, notes, ranked causes) |

**Ids.** Each finding has a content key built from its detector, category, scope (without the time range)
and metric name. When an analysis produces a finding with a known key, it keeps its id, status, cause, fix
and test result. New keys get the next free id, in order of impact
(affected share x severity weight x category weight).

**Statuses.** hone-lens sets `new`; a person sets the others with `ws.set_status(id, status, note="")`.
`dismissed` findings stay dismissed and are left out of reports. `fixed` findings are re-checked: when a
later analysis finds the same issue in runs that started after the fix was recorded, the status becomes
`regressed`. See [reports.md](reports.md).

**Causes.** A cause starts as `suspected` (stage 5). A replay test (stage 6) sets it to `confirmed` or
`refuted`, or leaves it `suspected` when the result is inconclusive. The finding's own status never changes
automatically; confirming it is a person's call.

## Budgets

The LLM stages (describe, review, label, test) take `budget=`:

```python
import hone_lens as tl

tl.Budget(usd=2.0, usd_per_1k_tokens=0.002)  # a money limit with a price for clients that report no cost
tl.Budget(tokens=50_000)  # a token limit (useful for local models)
tl.Budget(calls=40)  # a call limit
# Shorthands accepted wherever budget= is: 2.5 (USD), "2usd", "$2", "5000 tokens", "40 calls"
```

A call costs the client's reported `usage["cost_usd"]` when there is one, otherwise tokens x
`usd_per_1k_tokens` (default 0, so local models are free; limit them by tokens or calls). A USD limit
therefore only bites when the client reports `cost_usd` or the budget has a price: the bundled
OpenAI adapter reports tokens only, so give it `tl.Budget(usd=..., usd_per_1k_tokens=...)` or a token
or call limit. Replays are charged the `hone.models.cost_usd` of the replayed span when it has one. The limit is
checked *before* each call with an estimate, so a stage stops at most about one call past its limit. At
the limit a stage stops, keeps what it finished, and says so: `Report.budget_exhausted`, `Report.notes`,
`Report.cost_usd`, `Report.llm_calls` (and the same fields on `LabelReport`, `cost_usd` / `calls` on
`TestResult`). `BudgetExceeded` is handled inside the stages; it reaches you only if you drive a `Budget`
yourself.

One `Budget` object is a wallet: pass the same object to several calls to share one limit.

```python
import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, synthetic_runs

runs = synthetic_runs("demo", n_runs=200, plant=["homogeneity_from_example"])
ws = tl.Workspace("demo/lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
ws.ingest(f"hone:{runs.root}")

wallet = tl.Budget(calls=0)
report = ws.analyze("song_ideas", budget=wallet)
print(report.budget_exhausted, report.notes)
# True ['describe stopped: call budget of 0 spent; 1 clusters left undescribed']
assert report.budget_exhausted and report.findings  # the statistics and output findings are still there
```

## Errors

Every error hone-lens raises on purpose is a `hone_lens.errors.HoneLensError`. Two subclasses:
`SourceError` (a source could not be read: missing file, bad format, unknown source string) and
`BudgetExceeded` (see above). Model client failures during analysis are not raised: they become notes, and
the analysis goes on.
