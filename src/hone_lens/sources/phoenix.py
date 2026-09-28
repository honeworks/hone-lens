"""`PhoenixExport`: spans exported from Arize Phoenix (`get_spans_dataframe()` saved as Parquet or JSONL).

Phoenix records OpenInference attributes (`llm.model_name`, `llm.token_count.prompt`,
`llm.input_messages`, ...); they are mapped to the OpenTelemetry GenAI names hone-lens reads. Parquet needs
`pip install hone-lens[phoenix]` (pyarrow).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hone_lens.errors import SourceError
from hone_lens.mapping import as_dict, as_list, canonical_time, json_value, status_code
from hone_lens.sources._files import changed_files, read_json_or_lines

# OpenInference attribute -> OpenTelemetry GenAI attribute
OPENINFERENCE = {
    "llm.model_name": "gen_ai.request.model",
    "llm.provider": "gen_ai.provider.name",
    "llm.system": "gen_ai.provider.name",
    "llm.token_count.prompt": "gen_ai.usage.input_tokens",
    "llm.token_count.completion": "gen_ai.usage.output_tokens",
}
_PARAMS = {
    "temperature": "gen_ai.request.temperature",
    "seed": "gen_ai.request.seed",
    "max_tokens": "gen_ai.request.max_tokens",
}


class PhoenixExport:
    """Spans from one Phoenix export file (`.parquet` or `.jsonl`). Phoenix exports carry no service name,
    so the workflow name is `workflow` (default: the file name without extension).

    >>> source = PhoenixExport("export.parquet", workflow="song_ideas")  # doctest: +SKIP
    """

    def __init__(self, path: str | Path, workflow: str | None = None) -> None:
        self.path = Path(path)
        self.workflow = workflow or self.path.stem
        self.name = f"phoenix:{self.path.absolute()}"

    def spans(self, *, since: str | None = None) -> list[dict[str, Any]]:
        """Every span, or those starting at or after `since` (ISO-8601)."""
        spans = [self._span(row) for row in _rows(self.path)]
        return [s for s in spans if since is None or s["start_time"] >= since]

    def read_new(self, cursor: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Every span again when the file changed since `cursor` (the ingest deduplicates), else none."""
        changed, marks = changed_files([self.path.resolve()], cursor)
        return (self.spans() if changed else []), marks

    def _span(self, row: Mapping[str, Any]) -> dict[str, Any]:
        attributes = _attributes(row)
        try:
            return {
                "trace_id": str(row["context.trace_id"]),
                "span_id": str(row["context.span_id"]),
                "parent_span_id": row.get("parent_id") or None,
                "name": str(row["name"]),
                "kind": "client" if row.get("span_kind") == "LLM" else "internal",
                "start_time": canonical_time(row["start_time"]),
                "end_time": canonical_time(row["end_time"]) if row.get("end_time") else None,
                "status": {
                    "code": status_code(row.get("status_code")),
                    "message": str(row.get("status_message") or ""),
                },
                "attributes": attributes,
                "events": as_list(json_value(row.get("events"))),
                "resource": {"service.name": self.workflow},
                "links": [],
            }
        except (KeyError, ValueError) as e:
            raise SourceError(f"{self.path}: malformed Phoenix span ({e!r})") from e


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SourceError(f"no Phoenix export at {path}")
    if path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs] - optional extra
        except ImportError as e:
            raise SourceError("reading Parquet needs pyarrow: pip install 'hone-lens[phoenix]'") from e
        return pq.read_table(path).to_pylist()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return [as_dict(row) for row in read_json_or_lines(path, "a Phoenix JSONL export")]


def _attributes(row: Mapping[str, Any]) -> dict[str, Any]:
    """Flat `attributes.*` columns (or an `attributes` object), with OpenInference names mapped to GenAI."""
    raw = {
        k.removeprefix("attributes."): v
        for k, v in row.items()
        if k.startswith("attributes.") and v is not None
    }
    raw.update(as_dict(json_value(row.get("attributes"))))
    attributes = {OPENINFERENCE.get(k, k): v for k, v in raw.items()}
    if row.get("span_kind") == "LLM" or "llm.model_name" in raw:
        attributes.setdefault("gen_ai.operation.name", "chat")
    params = as_dict(json_value(raw.get("llm.invocation_parameters")))
    attributes |= {name: params[key] for key, name in _PARAMS.items() if key in params}
    for key, target, fallback in (
        ("llm.input_messages", "gen_ai.input.messages", "input.value"),
        ("llm.output_messages", "gen_ai.output.messages", "output.value"),
    ):
        messages = [_message(m) for m in as_list(json_value(raw.get(key)))]
        if not messages and isinstance(raw.get(fallback), str):
            messages = [
                {"role": "assistant" if key.startswith("llm.output") else "user", "content": raw[fallback]}
            ]
        if messages:
            attributes[target] = messages
    return attributes


def _message(value: Any) -> dict[str, Any]:
    m = as_dict(value)
    m = as_dict(m.get("message")) or m  # both {"message": {...}} and flat shapes
    return {
        "role": m.get("message.role", m.get("role", "")),
        "content": m.get("message.content", m.get("content", "")),
    }
