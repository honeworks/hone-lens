# Sources

A source is where hone-lens reads spans from. `Workspace.ingest(*sources)` accepts source strings and
source objects, any number at once, and returns one `Ingested(source, read, added)` per source: how many
spans it read and how many of those were new.

```python
import hone_lens as tl
from hone_lens.testing import synthetic_runs

runs = synthetic_runs("demo", n_runs=100)  # span stores, hone-flow run folders and an OTLP file
ws = tl.Workspace("demo/lens")
print(ws.ingest(f"hone:{runs.root}"))
# [Ingested(source='hone:/.../demo/runs', read=720, added=720)]
```

## Built-in sources

| String | Class | Notes |
|---|---|---|
| `"hone:<path>"` | `tl.sources.HoneSpanStore(path)` | a `spans.db` / `spans.jsonl` file, or a folder: every such file below it (e.g. `"hone:.hone"`) |
| `"otlp:<glob>"` | `tl.sources.OtlpJsonFiles(glob)` | OTLP/JSON files; one export object per file or one per line; `**` works |
| `"phoenix:<file>"` | `tl.sources.PhoenixExport(path, workflow=None)` | Phoenix `get_spans_dataframe()` saved as `.parquet` (needs `hone-lens[phoenix]`) or `.jsonl` |
| `"langfuse:<file>"` | `tl.sources.LangfuseExport(path, workflow=None)` | Langfuse observations: a JSON array, an API page `{"data": [...]}`, or JSONL |
| `"flow:<storage>/<workflow>"` | `hone_lens.adapters.flow.FlowRuns(storage, name)` | hone-flow runs through hone-flow's read API, on any storage it supports (local, `s3://...`); also fills `flow_steps`; needs `hone-lens[flow]` |

An unknown prefix or a path with no data raises `hone_lens.errors.SourceError` with a message that says
what was expected.

### honeworks span stores

`HoneSpanStore` reads the SQLite stores and JSONL files that hone-models and hone-select write
(`.hone/{models,select}/spans.db`), and the `spans.jsonl` file hone-flow keeps in every run folder
(`<storage>/<workflow>/runs/<run_id>/`; point it at a local `runs/` folder or the storage root). All use
records schema version 1. These records carry `hone.*` attributes, which give hone-lens the workflow,
step, run and item of every call, the prompt template version and **prompt sections**, selection scores
and gates, workflow step statuses (`hone.flow.status`), attempts, reuse and fork provenance, and GPU
leases. A store with a newer schema version is refused with a message to upgrade hone-lens.

### hone-flow runs

hone-flow keeps every run in its own folder: the run's spans in `spans.jsonl`, its step records in
`manifest.json` and `metadata.json` files. There are two ways in, and both give the same `steps`,
`selections` and `run_calls` rows:

- `"hone:<folder>"` reads every `spans.jsonl` below a local folder (a workflow's `runs/`, or the storage
  root). No hone-flow install needed.
- `"flow:<storage>/<workflow>"` (`FlowRuns(storage, name)`, extra `flow`) goes through hone-flow's public
  read API (`fk.open_runs(storage, name)`), so it works for runs on S3 and any storage hone-flow
  supports. The last path segment is the workflow name, the rest is the storage root:
  `"flow:s3://hone-flow/projects/oneshotstudio/song_video"`. Besides the spans it reads every run's **step
  records** into the `flow_steps` table: one row per (run, step, item) with `status` (including
  `skipped`, `blocked`, `awaiting_review`, `interrupted`), `attempt`, `attempts` and `attempt_statuses`
  (`failed`, `rejected`, `replaced`), `reused_from`, `fork_of`, `run_seed` (the manifest's `seed`),
  `review_decisions` (decision, actor, actor kind, time; review notes are not copied, only `review_note_present`) and
  `error`. A resumed run's rows are replaced
  when it is read again.

`FlowRuns` takes `history=` instead of opening storage itself: anything with hone-flow's
`runs(updated_since=)` and `open_run(run_id)`. `hone_lens.testing.FakeFlowRuns` is such an object (no
hone-flow needed), which is how this example runs anywhere:

```python
import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.testing import FakeFlowRuns

reviews = [
    {"decision": "rejected", "actor": "ana", "at": "2026-09-01T10:00:00Z", "attempt": 1},
    {"decision": "approved", "actor": "ana", "at": "2026-09-01T11:00:00Z", "attempt": 2},
]
history = FakeFlowRuns()  # stands in for fk.open_runs("s3://bucket/projects/studio", "songs")
history.add_run(
    "run-1",
    [
        {"step": "lyrics", "item": "01", "attempt": 2, "attempts": [{"attempt": 1, "status": "rejected"}]},
        {"step": "review", "item": "01", "kind": "gate", "reviews": reviews},
        {"step": "lyrics", "item": "02", "status": "failed", "error": {"message": "ValueError: empty"}},
        {"step": "review", "item": "02", "kind": "gate", "status": "blocked"},
    ],
    status="failed",
)
ws = tl.Workspace("songs/lens")
ws.ingest(FlowRuns("s3://bucket/projects/studio", "songs", history=history))
print(ws.db.query("SELECT step, item, status, attempt, attempt_statuses FROM flow_steps ORDER BY step, item"))
# [{'step': 'lyrics', 'item': '01', 'status': 'done', 'attempt': 2, 'attempt_statuses': '["rejected"]'},
#  {'step': 'lyrics', 'item': '02', 'status': 'failed', 'attempt': 1, 'attempt_statuses': '[]'},
#  {'step': 'review', 'item': '01', 'status': 'done', 'attempt': 1, 'attempt_statuses': '[]'},
#  {'step': 'review', 'item': '02', 'status': 'blocked', 'attempt': 1, 'attempt_statuses': '[]'}]
```

Without the `flow` extra, `hone_lens` and `FlowRuns(..., history=...)` still import; the `"flow:"` string
raises `SourceError` saying to install `hone-lens[flow]`.

### OpenTelemetry (OTLP JSON)

`OtlpJsonFiles` reads what an OpenTelemetry Collector file exporter writes (and any OTLP/JSON export),
from any instrumentation that follows the GenAI semantic conventions. Without `hone.*` attributes:

- the workflow is the resource attribute `service.name`,
- the step is the name of the model call's parent span,
- the run is the trace.

Older GenAI attribute names (`gen_ai.system`, `gen_ai.usage.prompt_tokens`,
`gen_ai.usage.completion_tokens`, `gen_ai.prompt.<n>.*`, prompt / completion events) are mapped to the
current ones. Statistics and output findings work on plain OTel traces; causes at the level of prompt
sections need `hone.models.prompt.sections`, and a finding says so when they are missing.

```python
import hone_lens as tl
from hone_lens.sources import OtlpJsonFiles
from hone_lens.testing import synthetic_runs

runs = synthetic_runs("otel", n_runs=100)
ws = tl.Workspace("otel/lens")
ws.ingest(OtlpJsonFiles("otel/runs-otlp/*.json"))  # the same as the string "otlp:otel/runs-otlp/*.json"
print(ws.db.query("SELECT workflow, step, count(*) AS calls FROM calls GROUP BY workflow, step"))
# [{'workflow': 'song_ideas', 'step': 'generate_idea', 'calls': 100}]
```

### Phoenix and Langfuse exports

Neither export records a service name, so the workflow name is the `workflow` argument; the string forms
(`"phoenix:<file>"`, `"langfuse:<file>"`) use the file name without its extension.

`PhoenixExport` maps OpenInference attributes to GenAI names: `llm.model_name`, `llm.provider` /
`llm.system`, `llm.token_count.prompt` / `.completion`, `llm.input_messages` / `llm.output_messages`
(or `input.value` / `output.value`), and `temperature`, `seed`, `max_tokens` from
`llm.invocation_parameters`. Attributes may be flat `attributes.*` columns or one `attributes` object.

`LangfuseExport` turns `GENERATION` observations into model calls (`model`, `usageDetails` or `usage`,
`modelParameters`, `input`, `output`), `promptName` / `promptVersion` into the prompt template and
version, and `calculatedTotalCost` into the call cost; `level: "ERROR"` marks a failed call. Langfuse ids
are not W3C hex ids, so trace and span ids are derived from them with SHA-256 (the same every read).

```python
import json
from pathlib import Path

import hone_lens as tl
from hone_lens.sources import LangfuseExport

observations = []
for i in range(3):
    trace = f"trace-{i}"
    observations += [
        {
            "id": f"{trace}-root",
            "traceId": trace,
            "type": "SPAN",
            "name": "answer_question",
            "startTime": f"2026-09-01T10:0{i}:00Z",
            "endTime": f"2026-09-01T10:0{i}:05Z",
        },
        {
            "id": f"{trace}-gen",
            "traceId": trace,
            "parentObservationId": f"{trace}-root",
            "type": "GENERATION",
            "name": "llm",
            "model": "gpt-4o-mini",
            "startTime": f"2026-09-01T10:0{i}:01Z",
            "endTime": f"2026-09-01T10:0{i}:04Z",
            "input": "What is the refund window?",
            "output": "30 days.",
            "usageDetails": {"input": 12, "output": 3},
            "promptName": "support",
            "promptVersion": 2,
        },
    ]
Path("observations.jsonl").write_text("\n".join(json.dumps(o) for o in observations))

ws = tl.Workspace("support/lens")
ws.ingest(LangfuseExport("observations.jsonl", workflow="support_bot"))
print(ws.db.query("SELECT workflow, step, model, template_version, input_tokens FROM calls LIMIT 1"))
# [{'workflow': 'support_bot', 'step': 'answer_question', 'model': 'gpt-4o-mini', 'template_version': '2',
#   'input_tokens': 12}]
```

## Incremental ingest

Run `ingest` as often as you like. Each source remembers where it stopped, per source name, in the
workspace:

- SQLite span stores: the store's `changes.seq` high-water mark (read before the spans, so spans written
  during an ingest are read next time),
- JSONL files (hone-flow's `spans.jsonl`, JSONL sinks): read on from the byte where the last read
  stopped, so a grown file costs only its new lines (a shorter, rewritten file is read again; an
  unfinished last line waits for the next ingest),
- other files (OTLP, Phoenix, Langfuse): a file is read again, whole, when its modification time or size
  changed,
- `FlowRuns`: the newest manifest `updated_at` seen; the next ingest asks hone-flow for
  `runs(updated_since=...)` only,
- any other source: `spans(since=...)` with the latest start time seen (minus the longest span duration
  seen, because parent spans are written after their children).

Spans are deduplicated by `span_id`: the first copy wins. Derived rows are rebuilt for every trace an
ingest touched, so the order in which you ingest stores that share traces does not matter.

```python
import hone_lens as tl
from hone_lens.testing import synthetic_runs

runs = synthetic_runs("inc", n_runs=100)
ws = tl.Workspace("inc/lens")
source = f"hone:{runs.root}"
print(ws.ingest(source)[0].added)  # 720: 7 spans per run, plus the resume call of every fifth run
print(ws.ingest(source)[0].added)  # 0: nothing new
synthetic_runs("inc", n_runs=10, seed=1)  # another seed appends 10 later runs to the same stores
print(ws.ingest(source)[0].added)  # 72
```

## Your own source

Anything with a `name` (a unique, stable string: it keys the ingest position) and a
`spans(*, since=None)` method that yields span dicts is a source (the `RecordSource` protocol). Spans
follow the [span shape](records.md#span-shape); `start_time` / `end_time` are ISO-8601 strings, ids are lowercase hex
(32 digits for traces, 16 for spans). Missing optional fields get defaults; see
[records.md](records.md) for the attributes hone-lens uses.

```python
import json

import hone_lens as tl
from hone_lens.testing import check_record_source


class MyLogs:
    """Model calls from an in-house log, one span per call."""

    name = "mylogs:support"

    def __init__(self, rows):
        self.rows = rows

    def spans(self, *, since=None):
        for i, row in enumerate(self.rows):
            if since is not None and row["time"] < since:
                continue
            yield {
                "trace_id": f"{i + 1:032x}",
                "span_id": f"{i + 1:016x}",
                "name": "chat",
                "start_time": row["time"],
                "end_time": row["time"],
                "status": {"code": "error" if row["error"] else "ok"},
                "resource": {"service.name": "support_bot"},
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": row["model"],
                    "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": row["answer"]}]),
                },
            }


rows = [
    {
        "time": f"2026-09-01T10:{m:02d}:00Z",
        "model": "gpt-4o-mini",
        "answer": f"Answer {m}",
        "error": m % 10 == 0,
    }
    for m in range(40)
]
source = MyLogs(rows)
check_record_source(source)  # the contract checker every source should pass
ws = tl.Workspace("mine/lens")
ws.ingest(source)
report = ws.analyze("support_bot", stages=("stats",))
print([f.title for f in report.findings])
# ['10% of support_bot (model gpt-4o-mini) calls failed']
```

`hone_lens.testing.FakeRecordSource(spans, name="fake")` wraps an in-memory list of spans the same way,
for tests.
