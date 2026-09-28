"""Normalize spans from any source to the records span shape (schema v1) and read common GenAI fields.

Only honeworks schema version "1" exists today, so version mapping is a no-op; plain OpenTelemetry GenAI
spans (no `hone.*` fields) are mapped by renaming older `gen_ai.*` attribute names to the current ones.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from hone_lens.errors import HoneLensError

# Older OpenTelemetry GenAI names -> current names (semantic conventions renamed them in 2024-2025).
RENAMED = {
    "gen_ai.system": "gen_ai.provider.name",
    "gen_ai.usage.prompt_tokens": "gen_ai.usage.input_tokens",
    "gen_ai.usage.completion_tokens": "gen_ai.usage.output_tokens",
}
_STATUS = {
    "ok": "ok",
    "error": "error",
    "unset": "unset",
    "status_code_ok": "ok",
    "status_code_error": "error",
    "status_code_unset": "unset",
    "1": "ok",
    "2": "error",
    "0": "unset",
}
_DURATION = re.compile(r"^(\d+)([dhm])$")
CHAT_OPERATIONS = frozenset({"chat", "text_completion", "generate_content"})


def as_dict(value: Any) -> dict[str, Any]:
    """`value` as a dict if it is a mapping, else `{}` (JSON payloads are untrusted)."""
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def as_list(value: Any) -> list[Any]:
    """`value` as a list if it is a list or tuple, else `[]`."""
    return list(cast(list[Any], value)) if isinstance(value, list | tuple) else []


def iso(t: datetime) -> str:
    """honeworks records time format: ISO-8601 UTC with milliseconds, e.g. `2026-09-27T14:03:11.120Z`."""
    t = t.astimezone(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_time(value: Any) -> datetime:
    """An ISO-8601 time as an aware datetime (no zone: UTC). Raises `ValueError` for anything else."""
    t = datetime.fromisoformat(str(value))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def canonical_time(value: Any) -> str:
    """Any ISO-8601 time string in honeworks records format, so times compare correctly as strings."""
    return iso(parse_time(value))


def since_time(since: str | datetime | None) -> str | None:
    """`since` as a records time string: ISO times pass through, `"30d"` / `"12h"` / `"45m"` count back
    from now."""
    if since is None:
        return None
    if isinstance(since, datetime):
        return iso(since if since.tzinfo else since.replace(tzinfo=UTC))
    match = _DURATION.match(since.strip())
    if match:
        unit = {"d": "days", "h": "hours", "m": "minutes"}[match.group(2)]
        return iso(datetime.now(UTC) - timedelta(**{unit: int(match.group(1))}))
    try:
        return canonical_time(since)
    except ValueError:
        raise HoneLensError(f"since={since!r} is not an ISO-8601 time or a duration like '30d'") from None


def status_code(value: Any) -> str:
    """`ok` / `error` / `unset` from OTel status codes in any spelling (`STATUS_CODE_OK`, `1`, `ok`)."""
    return _STATUS.get(str(value).lower(), "unset")


def normalize(span: Mapping[str, Any]) -> dict[str, Any]:
    """A span with every records field present, status as `{"code", "message"}`, current gen_ai names
    and canonical times. Raises `KeyError` / `ValueError` when a required field is missing or malformed."""
    attributes = as_dict(span.get("attributes"))
    for old, new in RENAMED.items():
        if old in attributes:
            attributes.setdefault(new, attributes.pop(old))
    events = as_list(span.get("events"))
    _add_legacy_messages(attributes, events)
    raw_status = span.get("status")
    status = as_dict(raw_status) if isinstance(raw_status, Mapping) else {"code": raw_status or "unset"}
    end_time = span.get("end_time")
    return {
        "trace_id": str(span["trace_id"]),
        "span_id": str(span["span_id"]),
        "parent_span_id": span.get("parent_span_id") or None,
        "name": str(span["name"]),
        "kind": str(span.get("kind") or "internal"),
        "start_time": canonical_time(span["start_time"]),
        "end_time": canonical_time(end_time) if end_time else None,
        "status": {
            "code": status_code(status.get("code", "unset")),
            "message": str(status.get("message") or ""),
        },
        "attributes": attributes,
        "events": events,
        "resource": as_dict(span.get("resource")),
        "links": as_list(span.get("links")),
    }


# Older GenAI conventions put prompts / completions in indexed attributes or in events.
_INPUT_EVENTS = {"gen_ai.system.message", "gen_ai.user.message", "gen_ai.assistant.message"}


def _add_legacy_messages(attributes: dict[str, Any], events: list[Any]) -> None:
    """Fill `gen_ai.input.messages` / `gen_ai.output.messages` from older shapes when they are missing:
    `gen_ai.prompt.<n>.role|content` / `gen_ai.completion.<n>.*` attributes, `gen_ai.content.prompt` /
    `gen_ai.content.completion` events, and `gen_ai.<role>.message` / `gen_ai.choice` events."""
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for event in map(as_dict, events):
        name, a = event.get("name"), as_dict(event.get("attributes"))
        if name in _INPUT_EVENTS:
            inputs.append({"role": str(name).split(".")[1], "content": a.get("content", "")})
        elif name == "gen_ai.content.prompt":
            inputs.append({"role": "user", "content": messages_text(a.get("gen_ai.prompt"))})
        elif name == "gen_ai.choice":
            message = as_dict(json_value(a.get("message")))
            outputs.append({"role": "assistant", "content": message.get("content", a.get("content", ""))})
        elif name == "gen_ai.content.completion":
            outputs.append({"role": "assistant", "content": messages_text(a.get("gen_ai.completion"))})
    for key, prefix, from_events in (
        ("gen_ai.input.messages", "gen_ai.prompt.", inputs),
        ("gen_ai.output.messages", "gen_ai.completion.", outputs),
    ):
        messages = _indexed_messages(attributes, prefix) or from_events
        if key not in attributes and messages:
            attributes[key] = messages


def _indexed_messages(attributes: Mapping[str, Any], prefix: str) -> list[dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for key, value in attributes.items():
        index, _, field = key.removeprefix(prefix).partition(".")
        if key.startswith(prefix) and index.isdigit() and field in ("role", "content"):
            by_index.setdefault(int(index), {})[field] = value
    return [by_index[i] for i in sorted(by_index)]


def json_value(value: Any) -> Any:
    """Decode attribute values that were stored as JSON strings (lists / objects); others pass through."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def messages_text(value: Any) -> str:
    """Plain text of a `gen_ai.input.messages` / `gen_ai.output.messages` value (both content styles)."""
    decoded = json_value(value)
    if isinstance(decoded, str):
        return decoded
    parts: list[str] = []
    for message in as_list(decoded):
        content = as_dict(message).get("content")
        pieces = [content] if isinstance(content, str) else as_list(content)
        for piece in pieces + as_list(as_dict(message).get("parts")):
            if isinstance(piece, str):
                parts.append(piece)
            elif as_dict(piece).get("type", "text") == "text":
                parts.append(str(as_dict(piece).get("content", as_dict(piece).get("text", ""))))
    return "\n".join(parts)


def output_text(span: Mapping[str, Any]) -> str:
    """The assistant text recorded on a call span (`gen_ai.output.messages`), or ''."""
    return messages_text(span.get("attributes", {}).get("gen_ai.output.messages"))


def is_model_call(span: Mapping[str, Any]) -> bool:
    """A chat-style model call: `hone.models.chat`, or any span with gen_ai chat request data."""
    attributes = span.get("attributes", {})
    if span.get("name") == "hone.models.chat":
        return True
    operation = attributes.get("gen_ai.operation.name")
    return operation in CHAT_OPERATIONS or (operation is None and "gen_ai.request.model" in attributes)


def finish_reason(attributes: Mapping[str, Any]) -> str | None:
    """The first `gen_ai.response.finish_reasons` entry (`stop`, `length`, ...), or None."""
    reasons = as_list(json_value(attributes.get("gen_ai.response.finish_reasons")))
    return str(reasons[0]) if reasons else None
