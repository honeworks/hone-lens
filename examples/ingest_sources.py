"""Ingest spans from every kind of source, and ingest again incrementally.

What: the built-in sources: honeworks span stores and hone-flow run folders (`hone:`), OpenTelemetry
OTLP/JSON files (`otlp:`), Arize Phoenix span exports (`phoenix:`) and Langfuse observation exports
(`langfuse:`), read into a workspace; and why one call should come from only one source.

How: `ws.ingest(source, ...)` takes source strings (`"<kind>:<path>"`) or source objects
(`tl.sources.HoneSpanStore(path)`, `OtlpJsonFiles(glob)`, `PhoenixExport(path, workflow=)`,
`LangfuseExport(path, workflow=)`) and returns one `Ingested(source, read, added)` per source. Each
source's position is stored in the workspace, so calling `ingest` again reads only what was added since;
spans already stored (same `span_id`) are skipped. (Your own source: see examples/own_adapters.py.)

Why: traces live wherever your tools wrote them. hone-lens reads them where they are, turns them into one
set of analytics tables (`calls`, `steps`, `selections`, `outputs`), and can be run after every batch of
runs without re-reading history.

Run: python examples/ingest_sources.py
"""

import json
import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.sources import PhoenixExport
from hone_lens.testing import synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # 1. Synthetic records: hone-models / hone-select span stores (SQLite), hone-flow run folders
    #    (<root>/runs/flows/song_ideas/runs/<run_id>/spans.jsonl) and the same model calls as a plain
    #    OpenTelemetry OTLP/JSON file (<root>/runs-otlp/traces-0.json).
    runs = synthetic_runs(root, n_runs=100)
    ws = tl.Workspace(root / "lens")

    # 2. honeworks records: one folder is enough, every spans.db / spans.jsonl below it is read.
    (hone,) = ws.ingest(f"hone:{runs.root}")
    print(f"hone:  read {hone.read}, added {hone.added}")  # 7 spans a run + every fifth run's resume call
    assert hone.added == 720

    # 3. OTLP/JSON files from any OpenTelemetry instrumentation (a glob; `**` works too). The synthetic
    #    OTLP file holds the *same* model calls as the hone stores, recorded by another tool, so it goes
    #    into a workspace of its own: spans are deduplicated by span id, and a copy of a call recorded by
    #    a second tool has another id, so it would be counted twice. Ingest each call from one source.
    otel_ws = tl.Workspace(root / "otel-lens")
    (otlp,) = otel_ws.ingest(f"otlp:{runs.otlp_path.parent}/*.json")
    print(f"otlp:  read {otlp.read}, added {otlp.added}")
    assert otlp.added == 200  # a parent span and a model call per run

    # 4. A Phoenix export (`px.Client().get_spans_dataframe()` saved as JSONL; Parquet needs the `phoenix`
    #    extra). OpenInference names (llm.model_name, llm.token_count.*) are mapped to gen_ai.* names.
    phoenix = root / "support_bot.jsonl"
    rows = [
        {
            "name": "ChatCompletion",
            "span_kind": "LLM",
            "context.trace_id": f"{i + 1:032x}",
            "context.span_id": f"{i + 1:016x}",
            "start_time": f"2026-09-01T10:00:{i:02d}Z",
            "end_time": f"2026-09-01T10:00:{i:02d}.800Z",
            "status_code": "OK",
            "attributes.llm.model_name": "gpt-4o-mini",
            "attributes.llm.token_count.prompt": 120,
            "attributes.llm.output_messages": [
                {"message.role": "assistant", "message.content": f"Answer {i}"}
            ],
        }
        for i in range(5)
    ]
    phoenix.write_text("\n".join(json.dumps(r) for r in rows))
    (px,) = ws.ingest(PhoenixExport(phoenix, workflow="support_bot"))  # the string form uses the file stem
    print(f"phoenix: added {px.added}")

    # 5. A Langfuse export (observations as JSONL). GENERATION observations become model calls; Langfuse
    #    ids are not W3C hex, so trace and span ids are derived from them with SHA-256.
    langfuse = root / "faq_bot.jsonl"
    observations = [
        {
            "id": f"gen-{i}",
            "traceId": f"trace-{i}",
            "type": "GENERATION",
            "name": "answer",
            "model": "gpt-4o-mini",
            "startTime": f"2026-09-01T11:00:{i:02d}Z",
            "endTime": f"2026-09-01T11:00:{i:02d}.500Z",
            "output": f"FAQ answer {i}",
            "promptName": "faq",
            "promptVersion": 3,
        }
        for i in range(4)
    ]
    langfuse.write_text("\n".join(json.dumps(o) for o in observations))
    (lf,) = ws.ingest(f"langfuse:{langfuse}")  # workflow = file stem: "faq_bot"
    print(f"langfuse: added {lf.added}")

    # Every source ends up in the same tables: one row per model call, whatever recorded it.
    per_workflow = ws.db.query(
        "SELECT workflow, count(*) AS calls FROM calls GROUP BY workflow ORDER BY workflow"
    )
    print(per_workflow)
    assert {r["workflow"]: r["calls"] for r in per_workflow} == {
        "faq_bot": 4,
        "song_ideas": 200,  # a generator and a judge call per run
        "support_bot": 5,
    }

    # 6. Incremental: nothing new means nothing read; new runs mean only the new spans are read.
    (unchanged,) = ws.ingest(f"hone:{runs.root}")
    assert unchanged.read == 0
    synthetic_runs(root, n_runs=10, seed=1)  # another seed appends 10 later runs (and traces-1.json)
    (again,) = ws.ingest(f"hone:{runs.root}")
    (otlp_again,) = otel_ws.ingest(f"otlp:{runs.otlp_path.parent}/*.json")
    print(f"after 10 more runs: hone added {again.added}, otlp added {otlp_again.added}")
    assert (again.added, otlp_again.added) == (72, 20)
