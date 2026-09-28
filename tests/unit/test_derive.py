import json

import hone_lens as tl
from hone_lens.derive import COLUMNS, derive
from hone_lens.mapping import normalize
from hone_lens.sources import HoneSpanStore, OtlpJsonFiles
from hone_lens.testing import synthetic_runs


def _traces(spans) -> dict[str, list[dict]]:
    traces: dict[str, list[dict]] = {}
    for s in map(normalize, spans):
        traces.setdefault(s["trace_id"], []).append(s)
    return traces


def _one_hone_trace(tmp_path, plant=()):
    runs = synthetic_runs(tmp_path, n_runs=1, plant=list(plant))
    (trace,) = _traces(HoneSpanStore(runs.root).spans()).values()
    return derive(trace)


def test_every_row_has_exactly_the_table_columns(tmp_path) -> None:
    rows = _one_hone_trace(tmp_path, plant=["vision_400", "nondeterministic_seed"])
    for table, table_rows in rows.items():
        assert table_rows, table
        assert all(set(r) == set(COLUMNS[table]) for r in table_rows), table


def test_honeworks_trace(tmp_path) -> None:
    rows = _one_hone_trace(tmp_path, plant=["vision_400"])
    context = {"workflow": "song_ideas", "step": "ideas", "run_id": "run-0-00000", "item": "item-0-00000"}
    generator, vision, judge = sorted(rows["calls"], key=lambda r: r["scorer"] or "")
    assert {k: generator[k] for k in context} == context  # the workflow comes from the flow store's run span
    assert generator["model"] == "gemma4-12b" and generator["scorer"] is None
    assert generator["template_version"] == "2" and generator["structured_path"] == "parsed"
    assert [s["id"] for s in json.loads(generator["sections"])] == ["role", "task"]
    assert generator["finish_reason"] == "stop" and generator["context_limit"] == 4096
    assert generator["latency_ms"] > 0 and generator["input_sha"]
    assert generator["status"] == "ok" and generator["error"] is None
    assert vision["scorer"] == "cover_art" and vision["status"] == "error" and "400" in vision["error"]
    assert judge["scorer"] == "quality" and judge["structured_path"] == "constrained"
    (output,) = rows["outputs"]  # judge and vision calls are not outputs
    assert output["span_id"] == generator["span_id"] and output["text"]
    (step,) = rows["steps"]
    assert step["step_version"] == "1" and json.loads(step["params"])
    assert (step["status"], step["executed"], step["attempt"]) == ("done", 1, 1)
    assert step["reused_from"] is None and step["fork_of"] is None
    (call,) = rows["run_calls"]
    assert (call["status"], call["fork_of"], call["warnings"], call["step"]) == (
        "completed",
        None,
        None,
        None,
    )
    kinds = sorted(r["kind"] for r in rows["selections"])
    assert kinds == ["decision", "run", "score"]
    score = next(r for r in rows["selections"] if r["kind"] == "score")
    assert score["scorer"] == "quality" and 0 <= score["value"] <= 1


def test_plain_otel_trace_uses_service_name_and_parent_step(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=1)
    (trace,) = _traces(OtlpJsonFiles(runs.otlp_path).spans()).values()
    rows = derive(trace)
    (call,) = rows["calls"]
    assert call["workflow"] == "song_ideas" and call["step"] == "generate_idea"
    assert call["run_id"] == call["trace_id"] and call["item"] is None
    assert call["provider"] == "ollama" and call["input_tokens"] > 0  # older names were mapped
    assert call["template_version"] is None and call["sections"] is None
    assert len(rows["outputs"]) == 1 and rows["steps"] == [] and rows["selections"] == []


def test_replayed_calls_are_not_outputs_and_human_gates_are_selections() -> None:
    base = {"trace_id": "a" * 32, "start_time": "2026-09-27T14:00:00Z", "end_time": None, "resource": {}}
    call = {
        **base,
        "span_id": "1" * 16,
        "name": "hone.models.chat",
        "attributes": {"hone.models.replay_of": "2" * 16, "gen_ai.output.messages": "hi"},
    }
    gate = {
        **base,
        "span_id": "3" * 16,
        "name": "hone.flow.gate",
        "attributes": {"hone.flow.gate.decision": "reject", "hone.flow.gate.actor": "ana"},
    }
    rows = derive([normalize(call), normalize(gate)])
    assert rows["outputs"] == [] and rows["calls"][0]["replay_of"] == "2" * 16
    assert rows["calls"][0]["latency_ms"] is None and rows["calls"][0]["workflow"] is None
    (human,) = rows["selections"]
    assert (human["kind"], human["decision"], human["actor"]) == ("human_gate", "reject", "ana")


def test_outputs_link_to_their_step_span(tmp_path) -> None:
    rows = _one_hone_trace(tmp_path)
    (output,) = rows["outputs"]
    (step,) = rows["steps"]
    assert output["step_span_id"] == step["span_id"]
    runs = synthetic_runs(tmp_path / "otlp", n_runs=1)
    (trace,) = _traces(OtlpJsonFiles(runs.otlp_path).spans()).values()
    (plain,) = derive(trace)["outputs"]
    root = next(s for s in trace if s["parent_span_id"] is None)
    assert plain["step_span_id"] == root["span_id"]  # plain OTel: the parent span


def test_selection_rows_read_candidates_gates_and_rankings() -> None:
    base = {"trace_id": "a" * 32, "start_time": "2026-09-27T14:00:00Z", "end_time": None, "resource": {}}
    spans = [
        {
            **base,
            "span_id": "1" * 16,
            "name": "hone.select.generate",
            "attributes": {"hone.select.candidate": json.dumps({"id": "c1", "meta": {}})},
        },
        {
            **base,
            "span_id": "2" * 16,
            "name": "hone.select.gate",
            "attributes": {
                "hone.select.gate": "len",
                "hone.select.gate.passed": False,
                "hone.select.gate.probability": 0.2,
            },
        },
        {
            **base,
            "span_id": "3" * 16,
            "name": "hone.select.decision",
            "attributes": {"hone.select.ranked": json.dumps([["c1", 0.9]]), "hone.select.escalated": True},
        },
    ]
    generate, gate, decision = derive([normalize(s) for s in spans])["selections"]
    assert generate["candidate_id"] == "c1"
    assert (gate["gate"], gate["passed"], gate["probability"]) == ("len", False, 0.2)
    assert json.loads(decision["ranked"]) == [["c1", 0.9]] and decision["escalated"] is True
    assert generate["ranked"] is None


def _flow_span(span_id: str, name: str, parent: str | None, attributes: dict, events=()) -> dict:
    return normalize(
        {
            "trace_id": "a" * 32,
            "span_id": span_id,
            "parent_span_id": parent,
            "name": name,
            "start_time": "2026-09-27T10:00:00Z",
            "end_time": "2026-09-27T10:00:01Z",
            "status": {"code": "ok", "message": ""},
            "attributes": {"hone.run_id": "fork-1", **attributes},
            "events": list(events),
        }
    )


def test_hone_flow_statuses_reuse_and_fork_provenance() -> None:
    warning = {"kind": "source_changed_version_unchanged", "step": "lyrics", "old": "a", "new": "b"}
    run = {"hone.flow.workflow": "songs", "hone.flow.status": "awaiting_review", "hone.flow.fork_of": "src-1"}
    trace = [
        _flow_span("0" * 16, "hone.flow.run", None, run, [{"name": "warning", "attributes": warning}]),
        _flow_span(
            "1" * 16,
            "hone.flow.step",
            "0" * 16,
            {
                "hone.step": "ideas",
                "hone.flow.status": "done",
                "hone.flow.reused_from": "src-1",
                "hone.flow.attempt": 1,
            },
        ),
        _flow_span(
            "2" * 16,
            "hone.flow.step",
            "0" * 16,
            {"hone.step": "lyrics", "hone.flow.status": "done", "hone.flow.attempt": 2},
        ),
        _flow_span(
            "3" * 16, "hone.flow.step", "0" * 16, {"hone.step": "render", "hone.flow.status": "skipped"}
        ),
        _flow_span(
            "4" * 16,
            "hone.flow.gate",
            "0" * 16,
            {"hone.step": "review", "hone.flow.status": "awaiting_review"},
        ),
        _flow_span(
            "5" * 16,
            "hone.flow.gate",
            None,
            {
                "hone.step": "review",
                "hone.flow.status": "pending",
                "hone.flow.gate.decision": "rejected",
                "hone.flow.gate.actor": "ana",
            },
        ),
    ]
    rows = derive(trace)
    steps = {r["step"]: r for r in rows["steps"]}
    assert {s: (r["status"], r["executed"]) for s, r in steps.items()} == {
        "ideas": ("done", 0),  # copied by the fork: not work done in this run
        "lyrics": ("done", 1),
        "render": ("skipped", 0),
        "review": ("awaiting_review", 0),  # the gate's own span is a step; the decision span is a review
    }
    assert steps["ideas"]["reused_from"] == "src-1" and steps["lyrics"]["attempt"] == 2
    assert {r["fork_of"] for r in rows["steps"]} == {"src-1"}
    assert all(r["workflow"] == "songs" and r["run_id"] == "fork-1" for r in rows["steps"])
    gates = [(r["kind"], r["gate"], r["decision"], r["actor"]) for r in rows["selections"]]
    assert gates == [("human_gate", "review", "rejected", "ana")]
    (call,) = rows["run_calls"]
    assert (call["status"], call["fork_of"]) == ("awaiting_review", "src-1")
    assert json.loads(call["warnings"]) == [warning]


def test_gpu_unloaded_as_recorded_by_hone_models_is_stored_as_a_count(tmp_path) -> None:
    """hone-models records `hone.models.gpu.unloaded` as the list of unloaded model names (found while
    building OneShotStudio: ingesting such a span failed with `type 'list' is not supported`)."""
    base = {"trace_id": "b" * 32, "start_time": "2026-09-27T14:00:00Z", "end_time": None, "resource": {}}
    spans = [
        {
            **base,
            "span_id": f"{i}" * 16,
            "name": "hone.models.chat",
            "attributes": {"hone.models.gpu.unloaded": value},
        }
        for i, value in enumerate([["gemma4-12b:latest", "qwen2.5vl:7b"], [], True, None], start=1)
    ]
    rows = derive([normalize(s) for s in spans])
    assert [r["gpu_unloaded"] for r in rows["calls"]] == [2, 0, 1, None]

    store = tmp_path / "spans.jsonl"
    store.write_text(
        "".join(json.dumps({**s, "parent_span_id": None, "status": {"code": "ok"}}) + "\n" for s in spans)
    )
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{store}")
    assert sorted(r["gpu_unloaded"] or 0 for r in ws.db.query("SELECT gpu_unloaded FROM calls")) == [
        0,
        0,
        1,
        2,
    ]


def test_recorded_run_and_step_seeds() -> None:
    trace = [
        _flow_span("0" * 16, "hone.flow.run", None, {"hone.flow.workflow": "songs", "hone.flow.seed": 7}),
        _flow_span("1" * 16, "hone.flow.step", "0" * 16, {"hone.step": "lyrics", "hone.flow.seed": 123}),
        _flow_span("2" * 16, "hone.flow.step", "0" * 16, {"hone.step": "render"}),
    ]
    rows = derive(trace)
    assert [r["seed"] for r in rows["run_calls"]] == [7]
    assert {r["step"]: r["seed"] for r in rows["steps"]} == {"lyrics": 123, "render": None}


def test_score_reasons_captured_or_not() -> None:
    def score(span_id: str, reason: object) -> dict:
        attributes = {"hone.select.score.value": 0.0, "hone.select.score.reason": reason}
        return _flow_span(span_id, "hone.select.score", None, attributes)

    trace = [
        score("1" * 16, "Not clean."),
        score("2" * 16, {"sha256": "ab" * 32, "len": 57}),  # content capture off
        score("3" * 16, ""),
    ]
    assert [r["reason"] for r in derive(trace)["selections"]] == [
        "Not clean.",
        "(not captured, 57 chars)",
        None,
    ]


def test_gate_actor_kind() -> None:
    decision = {
        "hone.step": "check",
        "hone.flow.gate.decision": "rejected",
        "hone.flow.gate.actor": "render-check",
    }
    trace = [
        _flow_span("1" * 16, "hone.flow.gate", None, decision | {"hone.flow.gate.actor_kind": "automated"}),
        _flow_span("2" * 16, "hone.flow.gate", None, decision),
    ]
    assert [r["actor_kind"] for r in derive(trace)["selections"]] == ["automated", None]
