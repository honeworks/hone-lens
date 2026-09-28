"""AC-14: the contract checkers pass on every shipped source and on the fake replayer, and they are exported
(`hone_lens.testing`) for packages that implement the ports."""

import json

import pytest

import hone_lens as tl
from hone_lens.testing import (
    FakeEmbedder,
    FakeReplayer,
    FakeTextClient,
    check_embedder,
    check_record_source,
    check_replayer,
    check_text_client,
    synthetic_runs,
)

pytestmark = pytest.mark.e2e


def _phoenix_rows(spans: list[dict]) -> list[dict]:
    return [
        {
            "name": s["name"],
            "span_kind": "LLM" if s["kind"] == "client" else "CHAIN",
            "parent_id": s["parent_span_id"],
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "status_code": "OK",
            "context.span_id": s["span_id"],
            "context.trace_id": s["trace_id"],
            "attributes.llm.model_name": s["attributes"].get("gen_ai.request.model"),
        }
        for s in spans
    ]


def _langfuse_observations(spans: list[dict]) -> list[dict]:
    return [
        {
            "id": f"obs-{s['span_id']}",
            "traceId": f"trace-{s['trace_id']}",
            "parentObservationId": f"obs-{s['parent_span_id']}" if s["parent_span_id"] else None,
            "type": "GENERATION" if s["kind"] == "client" else "SPAN",
            "name": s["name"],
            "startTime": s["start_time"],
            "endTime": s["end_time"],
            "model": s["attributes"].get("gen_ai.request.model"),
        }
        for s in spans
    ]


def test_ac14_every_source_passes_the_record_source_contract(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=30, plant=["vision_400"])
    otlp = tl.sources.OtlpJsonFiles(runs.otlp_path)
    plain = otlp.spans()
    phoenix = tmp_path / "phoenix.jsonl"
    phoenix.write_text("\n".join(json.dumps(r) for r in _phoenix_rows(plain)))
    langfuse = tmp_path / "langfuse.json"
    langfuse.write_text(json.dumps(_langfuse_observations(plain)))
    sources = [
        tl.sources.HoneSpanStore(runs.root),
        tl.sources.HoneSpanStore(runs.root / "models" / "spans.db"),
        otlp,
        tl.sources.PhoenixExport(phoenix),
        tl.sources.LangfuseExport(langfuse),
    ]
    for source in sources:
        check_record_source(source)
    ws = tl.Workspace(tmp_path / "lens")
    # the same spans through three tools: Phoenix keeps the OTel ids (deduplicated), Langfuse ids are its own
    assert [r.added for r in ws.ingest(*sources[2:])] == [60, 0, 60]


def test_ac14_fakes_pass_their_port_contracts(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=5, plant=["homogeneity_from_example"])
    call = next(
        s
        for s in tl.sources.HoneSpanStore(runs.root / "models" / "spans.db").spans()
        if not s["attributes"].get("hone.scorer")
    )
    for replayer in (FakeReplayer(), FakeReplayer.removing_section_reduces_similarity()):
        check_replayer(replayer)
        check_replayer(replayer, call)
    check_embedder(FakeEmbedder())
    check_embedder(FakeEmbedder.semantic())
    check_text_client(FakeTextClient())
    check_text_client(FakeTextClient.analyst())


def test_ac14_a_broken_replayer_fails_the_contract() -> None:
    class Forgetful(FakeReplayer):
        def replay_call(self, call_span, overrides, *, trace=None):
            span = super().replay_call(call_span, overrides, trace=trace)
            del span["attributes"]["hone.models.replay_of"]
            return span

    with pytest.raises(AssertionError, match="set hone.models.replay_of"):
        check_replayer(Forgetful())
