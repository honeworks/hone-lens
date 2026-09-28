"""Contract checkers for the ports hone-lens owns (design/current.md §6). Implementations run them to
show they fit.

Each checker raises `AssertionError` with a message when the implementation breaks the contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from hone_lens.ports import get

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def check_text_client(client: Any) -> None:
    """current.md §6: text result shape, schema handling, unknown params ignored."""
    r = client.complete([{"role": "user", "content": "Say OK."}])
    assert isinstance(get(r, "text"), str)
    assert get(r, "error") is None or isinstance(get(r, "error"), str)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    r = client.complete(
        [{"role": "user", "content": 'Return {"ok": true}.'}],
        schema=schema,
        trace={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
    )
    assert get(r, "parsed") is not None or get(r, "error")
    client.complete([{"role": "user", "content": "x"}], unknown_param_is_ignored=1)


def check_embedder(e: Any) -> None:
    """current.md §6: one normalized vector of `dimensions` floats per text; empty in, empty out."""
    v = e.embed(["a", "b"])
    assert len(v) == 2 and all(len(x) == e.dimensions for x in v)
    assert all(abs(sum(t * t for t in x) - 1.0) < 1e-3 for x in v)
    assert e.embed([]) == []


def check_record_source(source: Any) -> None:
    """current.md §6: a named source yielding honeworks records spans; `since` filters by start time."""
    assert isinstance(source.name, str) and source.name, "source.name must be a non-empty string"
    spans = list(source.spans())
    assert spans, "check_record_source needs a source with at least one span"
    for span in spans:
        _check_span(span)
    ids = [s["span_id"] for s in spans]
    assert len(ids) == len(set(ids)), "span ids must be unique within one read"
    times = sorted(str(s["start_time"]) for s in spans)
    since = times[len(times) // 2]
    later = list(source.spans(since=since))
    assert later, "spans(since=...) dropped spans that start at or after `since`"
    assert all(str(s["start_time"]) >= since for s in later), "spans(since=...) returned older spans"


def _check_span(span: Mapping[str, Any]) -> None:
    assert _HEX32.match(str(span["trace_id"])), f"trace_id must be 32 lowercase hex: {span['trace_id']!r}"
    assert _HEX16.match(str(span["span_id"])), f"span_id must be 16 lowercase hex: {span['span_id']!r}"
    parent = span.get("parent_span_id")
    assert parent is None or _HEX16.match(str(parent)), f"bad parent_span_id {parent!r}"
    assert isinstance(span["name"], str) and span["name"]
    assert isinstance(span["start_time"], str) and span["start_time"][:4].isdigit()
    assert isinstance(span.get("attributes", {}), Mapping)
    assert get(span.get("status") or {}, "code", "unset") in ("ok", "error", "unset")


def example_call_span() -> dict[str, Any]:
    """A recorded `hone.models.chat` span with prompt sections, for replay checks."""
    prompt = "You pitch song ideas.\n\nExample: a storm at sea."
    return {
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": None,
        "name": "hone.models.chat",
        "kind": "client",
        "start_time": "2026-09-27T14:03:11.120Z",
        "end_time": "2026-09-27T14:03:12.220Z",
        "status": {"code": "ok", "message": ""},
        "events": [],
        "resource": {},
        "links": [],
        "attributes": {
            "hone.schema_version": "1",
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "ollama",
            "gen_ai.request.model": "gemma4-12b:latest",
            "gen_ai.input.messages": json.dumps([{"role": "user", "content": prompt}]),
            "gen_ai.output.messages": json.dumps(
                [{"role": "assistant", "content": "A storm opens the song."}]
            ),
            "hone.models.prompt.sections": json.dumps(
                [
                    {"id": "role", "version": "1", "start": 0, "end": 21},
                    {"id": "format_example", "version": "3", "start": 23, "end": len(prompt)},
                ]
            ),
        },
    }


def check_replayer(replayer: Any, call_span: Mapping[str, Any] | None = None) -> None:
    """current.md §6: replaying a call returns a new call span that points back at the original."""
    original = dict(call_span or example_call_span())
    for overrides in (
        {"prompt.sections": {"format_example": None}},
        {"prompt.sections": {"format_example": "Example: a quiet morning."}},
        {"params": {"temperature": 0.2}},
    ):
        result: Any = replayer.replay_call(original, overrides, trace={"hone.lens.finding_id": "F-0001"})
        assert isinstance(result, Mapping), "replay_call must return a span mapping"
        new = cast(Mapping[str, Any], result)
        _check_span(new)
        assert new["span_id"] != original["span_id"], "the replayed call needs its own span id"
        attributes: Mapping[str, Any] = new.get("attributes", {})
        assert attributes.get("hone.models.replay_of") == original["span_id"], "set hone.models.replay_of"
        assert "gen_ai.output.messages" in attributes, "the replayed span must record its output"


def check_step_rerunner(
    rerunner: Any, run_id: str, step: str, items: Sequence[str], params: Mapping[str, Any] | None = None
) -> None:
    """current.md §6: `rerun(run_id, step, items, params)` returns the ids of the new run(s) it made (a
    hone-flow fork: one new run beside `run_id`). Needs a run `run_id` with `step` and `items`."""
    result: Any = rerunner.rerun(run_id, step, list(items), dict(params or {}))
    assert isinstance(result, list), "rerun must return a list of new run ids"
    new_ids = cast(list[Any], result)
    assert new_ids, "rerun returned no run id"
    assert all(isinstance(r, str) and r for r in new_ids), f"run ids must be non-empty strings: {new_ids!r}"
    assert run_id not in new_ids, "rerun must make a new run (a fork), not change the source run"
