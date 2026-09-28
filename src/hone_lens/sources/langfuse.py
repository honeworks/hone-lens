"""`LangfuseExport`: observations exported from Langfuse (JSON array, `{"data": [...]}` API pages, or JSONL).

Langfuse ids are not W3C hex ids, so trace and span ids are derived from them with SHA-256 (stable across
re-reads). GENERATION observations become GenAI model-call spans; `promptVersion` becomes the prompt
version, so regressions across Langfuse prompt versions are found too.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hone_lens.errors import SourceError
from hone_lens.mapping import as_dict, as_list, canonical_time
from hone_lens.sources._files import changed_files, read_json_or_lines

_HEX = re.compile(r"^[0-9a-f]+$")


class LangfuseExport:
    """Observations from one Langfuse export file. The workflow name is `workflow` (default: the file name
    without extension), since observations carry no service name.

    >>> source = LangfuseExport("observations.jsonl", workflow="song_ideas")  # doctest: +SKIP
    """

    def __init__(self, path: str | Path, workflow: str | None = None) -> None:
        self.path = Path(path)
        self.workflow = workflow or self.path.stem
        self.name = f"langfuse:{self.path.absolute()}"

    def spans(self, *, since: str | None = None) -> list[dict[str, Any]]:
        """Every observation as a span, or those starting at or after `since` (ISO-8601)."""
        spans = [self._span(o) for o in _observations(self.path)]
        return [s for s in spans if since is None or s["start_time"] >= since]

    def read_new(self, cursor: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Every span again when the file changed since `cursor` (the ingest deduplicates), else none."""
        changed, marks = changed_files([self.path.resolve()], cursor)
        return (self.spans() if changed else []), marks

    def _span(self, o: Mapping[str, Any]) -> dict[str, Any]:
        try:
            error = o.get("level") == "ERROR"
            parent = o.get("parentObservationId")
            return {
                "trace_id": _hex_id(str(o["traceId"]), 32),
                "span_id": _hex_id(str(o["id"]), 16),
                "parent_span_id": _hex_id(str(parent), 16) if parent else None,
                "name": str(o.get("name") or o.get("type") or "observation"),
                "kind": "client" if o.get("type") == "GENERATION" else "internal",
                "start_time": canonical_time(o["startTime"]),
                "end_time": canonical_time(o["endTime"]) if o.get("endTime") else None,
                "status": {"code": "error" if error else "ok", "message": str(o.get("statusMessage") or "")},
                "attributes": _attributes(o),
                "events": [],
                "resource": {"service.name": self.workflow},
                "links": [],
            }
        except (KeyError, ValueError) as e:
            raise SourceError(f"{self.path}: malformed Langfuse observation ({e!r})") from e


def _observations(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SourceError(f"no Langfuse export at {path}")
    documents = read_json_or_lines(path, "a Langfuse JSON / JSONL export")
    data: Any = documents[0] if len(documents) == 1 else documents
    if isinstance(data, dict):
        data = as_dict(data).get("data", [data])  # an API page, or a single observation
    observations = [as_dict(o) for o in as_list(data)]
    if not observations:
        raise SourceError(f"{path} holds no Langfuse observations")
    return observations


def _hex_id(value: str, n: int) -> str:
    """`value` when it already is an n-digit hex id, else the first n hex digits of its SHA-256."""
    if len(value) == n and _HEX.match(value):
        return value
    return hashlib.sha256(value.encode()).hexdigest()[:n]


def _attributes(o: Mapping[str, Any]) -> dict[str, Any]:
    if o.get("type") != "GENERATION":
        return {"langfuse.type": o.get("type")}
    usage = as_dict(o.get("usageDetails")) or as_dict(o.get("usage"))
    params = as_dict(o.get("modelParameters"))
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": o.get("model"),
        "gen_ai.usage.input_tokens": usage.get("input", usage.get("promptTokens")),
        "gen_ai.usage.output_tokens": usage.get("output", usage.get("completionTokens")),
        "gen_ai.request.temperature": params.get("temperature"),
        "gen_ai.request.seed": params.get("seed"),
        "gen_ai.input.messages": _messages(o.get("input"), "user"),
        "gen_ai.output.messages": _messages(o.get("output"), "assistant"),
        "hone.models.prompt.template_id": o.get("promptName"),
        "hone.models.prompt.template_version": o.get("promptVersion"),
        "hone.models.cost_usd": o.get("calculatedTotalCost"),
    }
    return {k: v for k, v in attributes.items() if v is not None}


def _messages(value: Any, role: str) -> list[dict[str, Any]] | None:
    """Langfuse inputs / outputs are a string, a message, a list of messages, or `{"messages": [...]}`."""
    if value is None:
        return None
    if isinstance(value, str):
        return [{"role": role, "content": value}]
    messages = as_list(value) or as_list(as_dict(value).get("messages")) or [as_dict(value)]
    return [
        {"role": as_dict(m).get("role", role), "content": as_dict(m).get("content", "")} for m in messages
    ]
