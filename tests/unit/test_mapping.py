from datetime import UTC, datetime

import pytest

from hone_lens.mapping import canonical_time, iso, messages_text, normalize, status_code
from hone_lens.testing.contracts import example_call_span


def test_iso_is_utc_with_milliseconds() -> None:
    assert iso(datetime(2026, 9, 27, 14, 3, 11, 120999, tzinfo=UTC)) == "2026-09-27T14:03:11.120Z"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-27T14:03:11.120Z", "2026-09-27T14:03:11.120Z"),
        ("2026-09-27T16:03:11+02:00", "2026-09-27T14:03:11.000Z"),
        ("2026-09-27T14:03:11.5", "2026-09-27T14:03:11.500Z"),  # no zone -> UTC
        ("2026-09-27", "2026-09-27T00:00:00.000Z"),
    ],
)
def test_canonical_time(raw: str, expected: str) -> None:
    assert canonical_time(raw) == expected


def test_canonical_time_rejects_non_times() -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - fromisoformat's own message
        canonical_time("yesterday")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ok", "ok"),
        ("STATUS_CODE_ERROR", "error"),
        (1, "ok"),
        ("2", "error"),
        (0, "unset"),
        ("weird", "unset"),
    ],
)
def test_status_code(raw: object, expected: str) -> None:
    assert status_code(raw) == expected


def test_normalize_fills_defaults_and_renames_old_gen_ai_names() -> None:
    span = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "name": "chat x",
        "start_time": "2026-09-27T14:03:11+00:00",
        "status": "STATUS_CODE_OK",
        "attributes": {"gen_ai.system": "openai", "gen_ai.usage.prompt_tokens": 12},
    }
    out = normalize(span)
    assert out["parent_span_id"] is None
    assert out["kind"] == "internal"
    assert out["end_time"] is None
    assert out["start_time"] == "2026-09-27T14:03:11.000Z"
    assert out["status"] == {"code": "ok", "message": ""}
    assert out["attributes"] == {"gen_ai.provider.name": "openai", "gen_ai.usage.input_tokens": 12}
    assert out["events"] == [] and out["links"] == [] and out["resource"] == {}


def test_normalize_keeps_current_name_when_both_present() -> None:
    span = {**example_call_span(), "attributes": {"gen_ai.system": "old", "gen_ai.provider.name": "new"}}
    assert normalize(span)["attributes"] == {"gen_ai.provider.name": "new"}


def test_normalize_requires_ids() -> None:
    span = example_call_span()
    del span["trace_id"]
    with pytest.raises(KeyError):
        normalize(span)


def test_messages_text_reads_both_content_styles() -> None:
    old = '[{"role": "user", "content": "hi"}]'
    new = '[{"role": "user", "parts": [{"type": "text", "content": "there"}, {"type": "image"}]}]'
    assert messages_text(old) == "hi"
    assert messages_text(new) == "there"
    assert messages_text("plain text") == "plain text"
    assert messages_text(None) == ""


def _bare(attributes: dict, events: list | None = None) -> dict:
    span = {"trace_id": "a" * 32, "span_id": "b" * 16, "name": "llm", "start_time": "2026-09-27T14:00:00Z"}
    return normalize({**span, "attributes": attributes, "events": events or []})


def test_normalize_reads_indexed_prompt_and_completion_attributes() -> None:
    a = {
        "gen_ai.prompt.1.role": "user",
        "gen_ai.prompt.1.content": "second",
        "gen_ai.prompt.0.role": "system",
        "gen_ai.prompt.0.content": "first",
        "gen_ai.prompt.0.other": "ignored",
        "gen_ai.completion.0.content": "answer",
    }
    out = _bare(a)["attributes"]
    assert out["gen_ai.input.messages"] == [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "second"},
    ]
    assert messages_text(out["gen_ai.output.messages"]) == "answer"


def test_normalize_reads_message_events() -> None:
    events = [
        {"name": "gen_ai.system.message", "attributes": {"content": "be brief"}},
        {"name": "gen_ai.content.prompt", "attributes": {"gen_ai.prompt": "hello"}},
        {"name": "gen_ai.choice", "attributes": {"message": '{"content": "hi there"}'}},
        {"name": "gen_ai.content.completion", "attributes": {"gen_ai.completion": "and more"}},
        {"name": "unrelated"},
    ]
    out = _bare({}, events)["attributes"]
    assert out["gen_ai.input.messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    assert messages_text(out["gen_ai.output.messages"]) == "hi there\nand more"


def test_normalize_keeps_current_messages() -> None:
    a = {"gen_ai.input.messages": "current", "gen_ai.prompt.0.content": "old"}
    assert _bare(a)["attributes"]["gen_ai.input.messages"] == "current"
