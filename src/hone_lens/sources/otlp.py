"""`OtlpJsonFiles`: spans from OTLP/JSON trace exports (OTel Collector file exporter, any instrumentation)."""

from __future__ import annotations

import glob
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hone_lens.errors import SourceError
from hone_lens.mapping import as_dict, as_list, iso, status_code
from hone_lens.sources._files import changed_files, read_json_or_lines

_KINDS = ("unspecified", "internal", "server", "client", "producer", "consumer")


class OtlpJsonFiles:
    """Spans from OTLP/JSON files matching a glob. Each file holds one export object, or one per line.

    >>> source = OtlpJsonFiles("traces/*.json")  # doctest: +SKIP
    """

    def __init__(self, pattern: str | Path) -> None:
        self.pattern = str(pattern)
        self.name = f"otlp:{Path(self.pattern).absolute()}"

    def files(self) -> list[Path]:
        """The files matching the pattern (raises `SourceError` when there are none)."""
        found = sorted(
            Path(p).resolve() for p in glob.glob(self.pattern, recursive=True) if Path(p).is_file()
        )
        if not found:
            raise SourceError(f"no OTLP JSON files match {self.pattern!r}")
        return found

    def spans(self, *, since: str | None = None) -> list[dict[str, Any]]:
        """Every span, or those starting at or after `since` (ISO-8601)."""
        spans = [s for path in self.files() for s in read_otlp_file(path)]
        return [s for s in spans if since is None or s["start_time"] >= since]

    def read_new(self, cursor: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Spans of files that are new or changed since `cursor`, and the next cursor."""
        changed, marks = changed_files(self.files(), cursor)
        return [s for path in changed for s in read_otlp_file(path)], marks


def read_otlp_file(path: Path) -> list[dict[str, Any]]:
    """All spans in one OTLP/JSON file, in the records span shape."""
    payloads = read_json_or_lines(path, "OTLP JSON")
    try:
        return [span for payload in payloads for span in _payload_spans(as_dict(payload))]
    except (KeyError, TypeError, ValueError) as e:
        raise SourceError(f"{path} has a malformed OTLP span ({e!r})") from e


def _payload_spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for resource_spans in map(as_dict, as_list(payload.get("resourceSpans"))):
        resource = _attributes(as_dict(resource_spans.get("resource")).get("attributes"))
        scopes = as_list(resource_spans.get("scopeSpans")) or as_list(
            resource_spans.get("instrumentationLibrarySpans")  # pre-1.0 OTLP name
        )
        for scope in scopes:
            spans += [_span(as_dict(raw), resource) for raw in as_list(as_dict(scope).get("spans"))]
    return spans


def _span(raw: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
    status = as_dict(raw.get("status"))
    end = raw.get("endTimeUnixNano")
    return {
        "trace_id": str(raw["traceId"]).lower(),
        "span_id": str(raw["spanId"]).lower(),
        "parent_span_id": str(raw.get("parentSpanId") or "").lower() or None,
        "name": str(raw["name"]),
        "kind": _kind(raw.get("kind")),
        "start_time": _time(raw["startTimeUnixNano"]),
        "end_time": _time(end) if end else None,
        "status": {
            "code": status_code(status.get("code", "unset")),
            "message": str(status.get("message", "")),
        },
        "attributes": _attributes(raw.get("attributes")),
        "events": [_event(as_dict(e)) for e in as_list(raw.get("events"))],
        "resource": resource,
        "links": [_link(as_dict(link)) for link in as_list(raw.get("links"))],
    }


def _attributes(key_values: Any) -> dict[str, Any]:
    """OTLP `[{"key", "value": AnyValue}]` as a plain dict."""
    return {str(kv["key"]): _any_value(kv.get("value")) for kv in map(as_dict, as_list(key_values))}


def _any_value(any_value: Any) -> Any:
    """One OTLP `AnyValue` as plain Python (`intValue` strings become ints)."""
    v = as_dict(any_value)
    if "arrayValue" in v:
        return [_any_value(x) for x in as_list(as_dict(v["arrayValue"]).get("values"))]
    if "kvlistValue" in v:
        return _attributes(as_dict(v["kvlistValue"]).get("values"))
    if "intValue" in v:
        return int(v["intValue"])
    for key in ("stringValue", "boolValue", "doubleValue", "bytesValue"):
        if key in v:
            return v[key]
    return None


def _kind(kind: Any) -> str:
    if isinstance(kind, int) and 0 < kind < len(_KINDS):
        return _KINDS[kind]
    name = str(kind or "").lower().removeprefix("span_kind_")
    return name if name in _KINDS[1:] else "internal"


def _time(unix_nano: Any) -> str:
    seconds, nanos = divmod(int(unix_nano), 1_000_000_000)
    return iso(datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=nanos // 1000))


def _event(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": raw.get("name", ""),
        "time": _time(raw.get("timeUnixNano", 0)),
        "attributes": _attributes(raw.get("attributes")),
    }


def _link(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace_id": str(raw.get("traceId", "")).lower(),
        "span_id": str(raw.get("spanId", "")).lower(),
        "attributes": _attributes(raw.get("attributes")),
    }
