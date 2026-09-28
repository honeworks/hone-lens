import json
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")  # the phoenix extra
pq = pytest.importorskip("pyarrow.parquet")

import hone_lens as tl
from hone_lens.errors import SourceError
from hone_lens.sources import LangfuseExport, PhoenixExport, open_source


def phoenix_rows(n: int = 3) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "name": "ChatCompletion",
                "span_kind": "LLM",
                "parent_id": None,
                "start_time": f"2026-09-01 10:00:{i:02d}.250000+00:00",
                "end_time": f"2026-09-01 10:00:{i:02d}.900000+00:00",
                "status_code": "ERROR" if i == 2 else "OK",
                "status_message": "rate limited" if i == 2 else "",
                "context.span_id": f"{i + 1:016x}",
                "context.trace_id": f"{i + 1:032x}",
                "attributes.llm.model_name": "gpt-4o-mini",
                "attributes.llm.provider": "openai",
                "attributes.llm.token_count.prompt": 120,
                "attributes.llm.token_count.completion": 30,
                "attributes.llm.invocation_parameters": json.dumps({"temperature": 0.7, "seed": 3}),
                "attributes.llm.input_messages": [{"message.role": "user", "message.content": f"idea {i}"}],
                "attributes.llm.output_messages": [
                    {"message.role": "assistant", "message.content": f"a song {i}"}
                ],
                "attributes.input.value": f"idea {i}",
            }
        )
    return rows


def test_phoenix_parquet_maps_openinference_to_gen_ai(tmp_path: Path) -> None:
    path = tmp_path / "song_ideas.parquet"
    pq.write_table(pa.Table.from_pylist(phoenix_rows()), path)
    source = open_source(f"phoenix:{path}")
    assert isinstance(source, PhoenixExport) and source.name == f"phoenix:{path}"
    spans = source.spans()
    first = spans[0]
    assert first["kind"] == "client" and first["start_time"] == "2026-09-01T10:00:00.250Z"
    assert first["resource"] == {"service.name": "song_ideas"}
    a = first["attributes"]
    assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.request.model"] == "gpt-4o-mini"
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (120, 30)
    assert (a["gen_ai.request.temperature"], a["gen_ai.request.seed"]) == (0.7, 3)
    assert a["gen_ai.output.messages"] == [{"role": "assistant", "content": "a song 0"}]
    assert spans[2]["status"] == {"code": "error", "message": "rate limited"}
    assert [s["span_id"] for s in source.spans(since="2026-09-01T10:00:01.000Z")] == [
        f"{2:016x}",
        f"{3:016x}",
    ]

    ws = tl.Workspace(tmp_path / "lens")
    assert ws.ingest(source)[0].added == 3
    calls = ws.db.query("SELECT workflow, model, provider, output_tokens, status FROM calls ORDER BY span_id")
    assert calls[0] == {
        "workflow": "song_ideas",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "output_tokens": 30,
        "status": "ok",
    }
    assert ws.db.query("SELECT count(*) AS n FROM outputs")[0]["n"] == 3
    assert ws.ingest(source)[0].read == 0  # unchanged file: nothing read


def test_phoenix_jsonl_with_attribute_objects_and_plain_values(tmp_path: Path) -> None:
    row = {
        "name": "llm",
        "span_kind": "LLM",
        "start_time": "2026-09-01T10:00:00Z",
        "context.span_id": "a" * 16,
        "context.trace_id": "b" * 32,
        "attributes": {"llm.model_name": "m", "output.value": "plain answer"},
    }
    path = tmp_path / "export.jsonl"
    path.write_text(json.dumps(row) + "\n")
    (span,) = PhoenixExport(path, workflow="w").spans()
    assert span["attributes"]["gen_ai.output.messages"] == [{"role": "assistant", "content": "plain answer"}]
    assert span["resource"] == {"service.name": "w"} and span["end_time"] is None


def test_phoenix_errors(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="no Phoenix export"):
        PhoenixExport(tmp_path / "missing.parquet").spans()
    (tmp_path / "bad.jsonl").write_text("{nope")
    with pytest.raises(SourceError, match="not a Phoenix JSONL export"):
        PhoenixExport(tmp_path / "bad.jsonl").spans()
    (tmp_path / "ids.jsonl").write_text(json.dumps({"name": "x", "start_time": "2026-09-01"}) + "\n")
    with pytest.raises(SourceError, match="malformed Phoenix span"):
        PhoenixExport(tmp_path / "ids.jsonl").spans()


def langfuse_observations() -> list[dict]:
    return [
        {
            "id": "obs-root",
            "traceId": "trace-1",
            "type": "SPAN",
            "name": "pipeline",
            "startTime": "2026-09-01T10:00:00.000Z",
            "endTime": "2026-09-01T10:00:03.000Z",
        },
        {
            "id": "obs-gen",
            "traceId": "trace-1",
            "parentObservationId": "obs-root",
            "type": "GENERATION",
            "name": "idea",
            "startTime": "2026-09-01T10:00:00.500Z",
            "endTime": "2026-09-01T10:00:02.500Z",
            "model": "claude-x",
            "modelParameters": {"temperature": 0.9},
            "input": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "an idea"}],
            "output": {"role": "assistant", "content": "a storm song"},
            "usageDetails": {"input": 50, "output": 12},
            "promptName": "song_idea",
            "promptVersion": 3,
            "calculatedTotalCost": 0.002,
        },
        {
            "id": "obs-err",
            "traceId": "trace-1",
            "type": "GENERATION",
            "startTime": "2026-09-01T10:00:04.000Z",
            "level": "ERROR",
            "statusMessage": "timeout",
            "input": "plain prompt",
            "usage": {"promptTokens": 5, "completionTokens": 0},
        },
    ]


@pytest.mark.parametrize("shape", ["array", "api_page", "jsonl"])
def test_langfuse_export_shapes(tmp_path: Path, shape: str) -> None:
    obs = langfuse_observations()
    path = tmp_path / "song_ideas.json"
    text = {
        "array": json.dumps(obs),
        "api_page": json.dumps({"data": obs, "meta": {}}),
        "jsonl": "\n".join(map(json.dumps, obs)),
    }[shape]
    path.write_text(text)
    source = open_source(f"langfuse:{path}")
    assert isinstance(source, LangfuseExport)
    root, gen, err = source.spans()
    assert (
        len(root["trace_id"]) == 32
        and len(root["span_id"]) == 16
        and gen["parent_span_id"] == root["span_id"]
    )
    assert root["attributes"] == {"langfuse.type": "SPAN"} and root["kind"] == "internal"
    a = gen["attributes"]
    assert gen["kind"] == "client" and a["gen_ai.request.model"] == "claude-x"
    assert (
        a["gen_ai.usage.input_tokens"],
        a["gen_ai.usage.output_tokens"],
        a["gen_ai.request.temperature"],
    ) == (50, 12, 0.9)
    assert a["gen_ai.input.messages"][0] == {"role": "system", "content": "be brief"}
    assert a["gen_ai.output.messages"] == [{"role": "assistant", "content": "a storm song"}]
    assert (a["hone.models.prompt.template_version"], a["hone.models.cost_usd"]) == (3, 0.002)
    assert err["status"] == {"code": "error", "message": "timeout"} and err["name"] == "GENERATION"
    assert err["attributes"]["gen_ai.input.messages"] == [{"role": "user", "content": "plain prompt"}]
    assert err["attributes"]["gen_ai.usage.input_tokens"] == 5
    assert LangfuseExport(path).spans() == source.spans()  # ids are stable


def test_langfuse_into_a_workspace_and_errors(tmp_path: Path) -> None:
    path = tmp_path / "ideas.jsonl"
    path.write_text("\n".join(map(json.dumps, langfuse_observations())))
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(LangfuseExport(path, workflow="song_ideas"))
    calls = ws.db.query(
        "SELECT workflow, step, template_version, cost_usd, status FROM calls ORDER BY start_time"
    )
    assert calls[0] == {
        "workflow": "song_ideas",
        "step": "pipeline",
        "template_version": "3",
        "cost_usd": 0.002,
        "status": "ok",
    }
    assert calls[1]["status"] == "error"
    with pytest.raises(SourceError, match="no Langfuse export"):
        LangfuseExport(tmp_path / "missing.json").spans()
    (tmp_path / "bad.json").write_text("{nope\n")
    with pytest.raises(SourceError, match="not a Langfuse JSON / JSONL export"):
        LangfuseExport(tmp_path / "bad.json").spans()
    (tmp_path / "one.jsonl").write_text(json.dumps(langfuse_observations()[0]) + "\n")
    assert len(LangfuseExport(tmp_path / "one.jsonl").spans()) == 1  # one line is one observation, not a page
    (tmp_path / "empty.json").write_text("[]")
    with pytest.raises(SourceError, match="holds no Langfuse observations"):
        LangfuseExport(tmp_path / "empty.json").spans()
    (tmp_path / "noid.json").write_text(json.dumps([{"traceId": "t", "startTime": "2026-09-01"}]))
    with pytest.raises(SourceError, match="malformed Langfuse observation"):
        LangfuseExport(tmp_path / "noid.json").spans()
