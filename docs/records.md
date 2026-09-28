# Records reference

hone-lens **reads** spans; it **writes no spans**. Its own results (findings, clusters, embeddings,
taxonomies, labels) live in the workspace database. This page lists what it reads, how it maps it, and
what it leaves behind.

## Span shape

Every source yields spans in the honeworks records shape (schema version 1), the same fields as an
OpenTelemetry span:

| Field | Notes |
|---|---|
| `trace_id` | 32 lowercase hex digits |
| `span_id` | 16 lowercase hex digits; the deduplication key |
| `parent_span_id` | or `None` |
| `name` | e.g. `hone.models.chat`, `hone.flow.step`, or any name |
| `kind` | `internal`, `client`, ... (default `internal`) |
| `start_time`, `end_time` | ISO-8601; stored as UTC with milliseconds, e.g. `2026-09-27T14:03:11.120Z` |
| `status` | `{"code": "ok" \| "error" \| "unset", "message": ...}`; OTel spellings (`STATUS_CODE_ERROR`, `2`) are accepted |
| `attributes` | a flat mapping; JSON strings for lists and objects are decoded when read |
| `events`, `links`, `resource` | lists / mapping; `resource["service.name"]` names the workflow of plain OTel traces |

honeworks stores carry `hone.schema_version` (JSONL spans) or a `schema_version` row in their `meta` table
(SQLite). hone-lens v0.1 reads version `"1"` and refuses others with a message to upgrade.

## Which spans are model calls

A span is a model call when its name is `hone.models.chat`, or its `gen_ai.operation.name` is `chat`,
`text_completion` or `generate_content`, or it has no operation name but has `gen_ai.request.model`.
A call is an **output** to analyze when it has output text, is not a judge call (`hone.scorer` unset) and
is not a replay (`hone.models.replay_of` unset).

## Attributes read

### Context (every derived table)

| Attribute | Used as | Fallback |
|---|---|---|
| `hone.flow.workflow` (on any span of the trace) | `workflow` | resource `service.name` |
| `hone.step` | `step` | the parent span's name (spans without `hone.run_id`) |
| `hone.run_id` | `run_id` | the trace id |
| `hone.item` | `item` | |

### OpenTelemetry GenAI (`calls`, `outputs`)

| Attribute | Used for |
|---|---|
| `gen_ai.request.model`, `gen_ai.response.model` | `model` (after `hone.models.model_id`) |
| `gen_ai.provider.name` | `provider` |
| `gen_ai.input.messages` | the prompt text (its hash finds repeated prompts; replayers rebuild it) |
| `gen_ai.output.messages` | the output text |
| `gen_ai.response.finish_reasons` | `finish_reason` (first entry) |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | token counts |
| `gen_ai.request.temperature`, `gen_ai.request.seed` | parameters (causes, unseeded repeats) |
| span status, duration, `retry` events | `status`, `error`, `latency_ms`, `retries` |

Older names are mapped on read: `gen_ai.system` -> `gen_ai.provider.name`,
`gen_ai.usage.prompt_tokens` / `completion_tokens` -> `input_tokens` / `output_tokens`, indexed
`gen_ai.prompt.<n>.role|content` and `gen_ai.completion.<n>.*` attributes and the
`gen_ai.content.prompt`, `gen_ai.content.completion`, `gen_ai.<role>.message` and `gen_ai.choice` events ->
`gen_ai.input.messages` / `gen_ai.output.messages`. Phoenix (OpenInference) and Langfuse fields are mapped
as described in [sources.md](sources.md#phoenix-and-langfuse-exports).

### hone-models (`calls`)

| Attribute | Used for |
|---|---|
| `hone.models.model_id` | `model` |
| `hone.scorer` | marks judge calls (`scorer`) |
| `hone.models.prompt.template_id`, `hone.models.prompt.template_version` | prompt template and version (regressions, causes) |
| `hone.models.prompt.sections` | section ids, versions and character ranges (closeness, section causes, replay variants) |
| `hone.models.structured.path` | `retried`, `repaired`, `failed` count as repairs; `failed` judge calls explain zero-score spikes |
| `hone.models.context.limit` | prompts near the context limit (truncation) |
| `hone.models.cost_usd` | call cost (`cost_latency`) |
| `hone.models.gpu.lease_wait_ms`, `hone.models.gpu.vram_after_mb`, `hone.models.gpu.unloaded` | `gpu_thrash` |
| `hone.models.replay_of` | marks replayed calls (left out of every detector) |

### hone-flow (`steps`, `selections`, `run_calls`)

hone-flow writes its spans into each run folder's `spans.jsonl` (read by `hone:` or the `flow:` source):
one `hone.flow.run` span per run / resume / fork call, one `hone.flow.step` (or `hone.flow.gate`) span per
(step, item) a call touched, and one `hone.flow.gate` span per review decision.

| Attribute | Used for |
|---|---|
| `hone.flow.workflow` | the workflow of every call in the trace |
| `hone.flow.status` | step status in `steps` (`done`, `failed`, `skipped`, `blocked`, `awaiting_review`, `interrupted`, `pending`): only `done` / `failed` steps that ran count in failure rates and durations; run status in `run_calls` |
| `hone.flow.attempt` | `steps.attempt` (retries and rejected attempts count up) |
| `hone.flow.reused_from` | `steps.reused_from`: a fork copied this step's output from that run (not counted as work done) |
| `hone.flow.fork_of` (on `hone.flow.run`) | `run_calls.fork_of` and `steps.fork_of` |
| `warning` events on `hone.flow.run` (`kind: source_changed_version_unchanged`, `step`) | `run_calls.warnings`, `reuse_health` |
| `hone.flow.step_version` | code version (regressions, causes) |
| `hone.flow.source_hash` | stored in `steps` |
| `hone.flow.params` | step parameters (causes; a `seed` parameter silences the unseeded-repeat smell) |
| `hone.flow.seed` (on `hone.flow.run` or `hone.flow.step`) | `run_calls.seed` / `steps.seed`: a run or step that records its seed is reproducible, so repeated prompts with other seeds in it (best-of-N) are not an unseeded-repeat smell |
| `hone.flow.deterministic` | stored in `steps` |
| `system.cpu.utilization.mean`, `system.memory.usage.peak_mb`, `hone.flow.gpu.memory.peak_mb`, `hone.flow.gpu.utilization.mean` | stored in `steps` |
| `hone.flow.gate.decision`, `hone.flow.gate.actor` (spans `hone.flow.gate`; the gate is `hone.step`) | `human_override` (gate spans without a decision are the gate pausing, not a review) |
| `hone.flow.gate.actor_kind` (`person` or `automated`) | `selections.actor_kind`: decisions of an `automated` reviewer (a script behind the gate API) are measured as rounds until approved, not as decisions by people; missing means a person |

hone-flow's step records (`metadata.json`, read through its API by the `flow:` source) fill `flow_steps`:
earlier attempts with their statuses, review decisions, `reused_from`, the run's `fork_of` and its seed
(the manifest's `seed`, `run_seed`; hone-flow derives every step's `ctx.seed` from it)
([sources.md](sources.md#hone-flow-runs)). hone-lens reads no `hone.flow.cache.*` attributes and no
`.hone/flow/spans.db`: the run-folder hone-flow has neither.

### hone-select (`selections`)

| Attribute | Used for |
|---|---|
| span names `hone.select.run`, `.generate`, `.score`, `.pairwise`, `.gate`, `.decision` | `kind` |
| `hone.select.candidate` (JSON `{id, ...}`), `hone.candidate_id` | `candidate_id` |
| `hone.select.scorer` (or `hone.scorer`), `hone.select.score.value`, `.confidence`, `.error` | scores: zero spikes, missing scores, judge disagreement, score regressions |
| `hone.select.score.reason` | `selections.reason` (text, or `(not captured, N chars)` when hone-select recorded only its hash): a zero with a reason and a confidence and no error is the judge's answer (`floor_score_rate`), not a failure |
| `hone.select.gate`, `hone.select.gate.passed`, `hone.select.gate.probability` | gate rejections |
| `hone.select.winner_id`, `hone.select.ranked`, `hone.select.fallback_used`, `hone.select.escalated` | winner margins, fallbacks |

## What hone-lens writes

**No spans.** hone-lens has no span sink.

**Replays.** A replay test calls your `Replayer` (for example hone-models') once per sampled call and
variant, with a trace context `{"traceparent": "00-<trace>-<span>-01", "hone.lens.finding_id": "F-0001"}`.
All LLM and replay calls of one `explain` or `test` share that trace. The replayer records the new calls
as usual (with `hone.models.replay_of`), so replays show up in your own span stores, marked with the
finding they test. When such stores are ingested again, replays are recognized by
`hone.models.replay_of` and kept out of the analysis.

```python
import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, synthetic_runs

runs = synthetic_runs("demo", n_runs=200, plant=["homogeneity_from_example"])
replayer = FakeReplayer.removing_section_reduces_similarity()  # records every call it gets
ws = tl.Workspace("demo/lens", embedder=FakeEmbedder.semantic(), replayer=replayer)
ws.ingest(f"hone:{runs.root}")
(finding,) = ws.analyze("song_ideas").findings
ws.test(finding.id, variants=1, samples=5)
call = replayer.calls[0]
print(call["overrides"])  # {'prompt.sections': {'format_example': None}}
print(call["trace"]["hone.lens.finding_id"])  # F-0001
assert all(c["trace"]["hone.lens.finding_id"] == finding.id for c in replayer.calls)
assert len({c["trace"]["traceparent"] for c in replayer.calls}) == 1  # one trace per test() call
```

**The workspace database** `<workspace>/lens.db` (SQLite) holds:

| Table | Contents |
|---|---|
| `spans` | every ingested span (records columns, JSON `attributes`, plus `source`) |
| `calls`, `steps`, `selections`, `outputs`, `run_calls` | the derived analytics tables ([detectors.md](detectors.md#analytics-tables)) |
| `flow_steps` | hone-flow step records from the `flow:` source, one row per (run, step, item) |
| `cursors` | the incremental ingest position per source name |
| `findings`, `affected` | every finding with its status, cause, fix, test result; every affected span id |
| `embeddings` | cached vectors per (embedder `model_id`, text sha256) |
| `clusters` | output clusters with members and descriptions |
| `taxonomies`, `labels` | reviewed taxonomies and output labels (`human` / `llm`) |

The database is yours to query (`ws.db.query(sql)`), but its layout is not a stable interface in v0.1;
use the API and the JSON report for integrations.
