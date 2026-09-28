"""Write spans as an OTLP/JSON trace export (OTel file exporter format). Used by the synthetic runs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

_KINDS = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}
_CODES = {"unset": 0, "ok": 1, "error": 2}


def _otlp_value(value: Any) -> dict[str, Any]:
    """One attribute value in OTLP/JSON `AnyValue` form."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list | tuple):
        return {"arrayValue": {"values": [_otlp_value(v) for v in value]}}  # pyright: ignore[reportUnknownVariableType]
    return {"stringValue": str(value)}


def _nanos(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000) * 1_000_000)


def _span(s: Mapping[str, Any]) -> dict[str, Any]:
    status: Mapping[str, Any] = s.get("status") or {}
    return {
        "traceId": s["trace_id"],
        "spanId": s["span_id"],
        "parentSpanId": s.get("parent_span_id") or "",
        "name": s["name"],
        "kind": _KINDS[s.get("kind", "internal")],
        "startTimeUnixNano": _nanos(s["start_time"]),
        "endTimeUnixNano": _nanos(s["end_time"]),
        "attributes": [{"key": k, "value": _otlp_value(v)} for k, v in s.get("attributes", {}).items()],
        "status": {"code": _CODES[status.get("code", "unset")], "message": status.get("message", "")},
    }


def write_otlp(path: Path, spans: Iterable[Mapping[str, Any]], *, service: str) -> None:
    """Write one `{"resourceSpans": [...]}` line (JSON Lines, like the OTel Collector file exporter)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                "scopeSpans": [{"scope": {"name": "synthetic"}, "spans": [_span(s) for s in spans]}],
            }
        ]
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
