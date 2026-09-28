import math

from hone_lens.mapping import output_text
from hone_lens.testing import FakeEmbedder, FakeRecordSource, FakeReplayer, FakeStepRerunner, FakeTextClient
from hone_lens.testing._otlp_writer import _otlp_value
from hone_lens.testing.contracts import example_call_span


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hash_embedder_is_deterministic_and_unit_length() -> None:
    e = FakeEmbedder(dimensions=16)
    a, b = e.embed(["storm at sea", "storm at sea"])
    assert a == b and len(a) == 16 and math.isclose(sum(x * x for x in a), 1.0)
    assert e.calls == [["storm at sea", "storm at sea"]]


def test_semantic_embedder_makes_shared_words_similar() -> None:
    e = FakeEmbedder.semantic()
    storm1, storm2, other = e.embed(
        [
            "A storm opens the song, Captain Mara",
            "Captain Rook and the storm opens",
            "a baker counts the days",
        ]
    )
    assert _cos(storm1, storm2) > 0.6 > _cos(storm1, other)
    assert e.embed([""])[0][0] == 1.0  # empty text still yields a unit vector


def test_text_client_responses_in_order_last_repeats() -> None:
    c = FakeTextClient(["one", "two"])
    assert [c.complete([{"role": "user", "content": "x"}]).text for _ in range(3)] == ["one", "two", "two"]
    assert len(c.calls) == 3


def test_text_client_schema_parsing_and_errors() -> None:
    c = FakeTextClient([{"ok": True}, "not json"])
    r = c.complete([{"role": "user", "content": "hi"}], schema={"type": "object"})
    assert r.parsed == {"ok": True} and r.error is None and r.usage["output_tokens"] > 0
    r = c.complete([{"role": "user", "content": "hi"}], schema={"type": "object"})
    assert r.parsed is None and r.error


def test_text_client_rule_records_params_and_trace() -> None:
    c = FakeTextClient(rule=lambda messages, schema: messages[-1]["content"].upper())
    assert c.complete([{"role": "user", "content": "loud"}], temperature=0, trace={"a": "b"}).text == "LOUD"
    assert c.calls[0]["params"] == {"temperature": 0} and c.calls[0]["trace"] == {"a": "b"}


def test_text_client_dict_without_schema_and_null_with_schema() -> None:
    c = FakeTextClient([{"x": 1}, "null"])
    r = c.complete([{"role": "user", "content": "x"}])
    assert r.text == '{"x": 1}' and r.parsed is None
    r = c.complete([{"role": "user", "content": "x"}], schema={"type": "object"})
    assert r.parsed is None and r.error


def test_default_replayer_returns_the_original_output() -> None:
    span = example_call_span()
    new = FakeReplayer().replay_call(span, {"prompt.sections": {"format_example": None}})
    assert output_text(new) == output_text(span) == "A storm opens the song."
    assert new["attributes"]["hone.models.replay_of"] == span["span_id"]


def test_section_replayer_changes_output_only_for_its_section() -> None:
    r = FakeReplayer.removing_section_reduces_similarity()
    span = example_call_span()
    assert output_text(r.replay_call(span, {})) == output_text(span)
    assert output_text(r.replay_call(span, {"prompt.sections": {"role": None}})) == output_text(span)
    changed = output_text(r.replay_call(span, {"prompt.sections": {"format_example": None}}))
    assert changed != output_text(span) and "chorus" in changed
    again = output_text(r.replay_call(span, {"prompt.sections": {"format_example": None}}))
    assert again == changed  # deterministic per (span, overrides)
    other = output_text(
        r.replay_call({**span, "span_id": "1" * 16}, {"prompt.sections": {"format_example": None}})
    )
    assert other != changed
    assert len(r.calls) == 5


def test_replayer_records_trace_and_applies_it() -> None:
    r = FakeReplayer()
    trace = {"traceparent": "00-" + "c" * 32 + "-" + "d" * 16 + "-01", "hone.lens.finding_id": "F-0007"}
    new = r.replay_call(example_call_span(), {"model": "qwen3-8b"}, trace=trace)
    assert r.calls[0]["trace"] == trace and r.calls[0]["overrides"] == {"model": "qwen3-8b"}
    assert new["trace_id"] == "c" * 32 and new["parent_span_id"] == "d" * 16
    assert new["attributes"]["hone.lens.finding_id"] == "F-0007"
    assert new["attributes"]["gen_ai.request.model"] == "qwen3-8b"


def test_otlp_value_types() -> None:
    assert _otlp_value(True) == {"boolValue": True}
    assert _otlp_value(3) == {"intValue": "3"}
    assert _otlp_value(0.5) == {"doubleValue": 0.5}
    assert _otlp_value(["a"]) == {"arrayValue": {"values": [{"stringValue": "a"}]}}


def test_output_text_reads_parts_and_missing() -> None:
    span = {
        "attributes": {
            "gen_ai.output.messages": [{"role": "assistant", "parts": [{"type": "text", "content": "hi"}]}]
        }
    }
    assert output_text(span) == "hi"
    assert output_text({"attributes": {}}) == ""


def test_step_rerunner_and_record_source() -> None:
    r = FakeStepRerunner()
    assert r.rerun("run-1", "ideas", ["a", "b"], {"seed": 1}) == ["run-1-rerun-1"]
    assert r.calls == [("run-1", "ideas", ["a", "b"], {"seed": 1})]
    source = FakeRecordSource([{"start_time": "2026-01-01"}, {"start_time": "2026-02-01"}])
    assert len(source.spans()) == 2 and len(source.spans(since="2026-01-15")) == 1
