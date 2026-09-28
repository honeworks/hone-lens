"""Record sources: where spans come from (current.md §6 `RecordSource`).

Source strings for `Workspace.ingest`: `"hone:<path>"` (honeworks span stores and hone-flow run folders),
`"otlp:<glob>"` (OTLP/JSON files), `"phoenix:<file>"` (Phoenix span exports), `"langfuse:<file>"`
(Langfuse observation exports) and `"flow:<storage>/<workflow>"` (hone-flow's read API; extra `flow`).
Any object with `name` and `spans(since=...)` works too.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from hone_lens.errors import SourceError
from hone_lens.mapping import iso, normalize, parse_time
from hone_lens.ports import RecordSource
from hone_lens.sources.hone import HoneSpanStore
from hone_lens.sources.langfuse import LangfuseExport
from hone_lens.sources.otlp import OtlpJsonFiles
from hone_lens.sources.phoenix import PhoenixExport


def _flow(target: str) -> RecordSource:
    from hone_lens.adapters.flow import flow_source  # noqa: PLC0415 - the adapter of an optional extra

    return flow_source(target)


KINDS: dict[str, Callable[[str], RecordSource]] = {
    "hone": HoneSpanStore,
    "otlp": OtlpJsonFiles,
    "phoenix": PhoenixExport,
    "langfuse": LangfuseExport,
    "flow": _flow,
}
USAGE = "'hone:<path>', 'otlp:<glob>', 'phoenix:<file>', 'langfuse:<file>', 'flow:<storage>/<workflow>'"
Cursor = dict[str, Any]
ReadNew = Callable[[Mapping[str, Any]], tuple[list[Mapping[str, Any]], Cursor]]

__all__ = [
    "KINDS",
    "HoneSpanStore",
    "LangfuseExport",
    "OtlpJsonFiles",
    "PhoenixExport",
    "open_source",
    "read_new",
]


def open_source(spec: str) -> RecordSource:
    """The source a string names, e.g. `"hone:.hone"` or `"otlp:traces/*.json"`."""
    kind, _, target = spec.partition(":")
    if kind not in KINDS or not target:
        raise SourceError(f"unknown source {spec!r}; use one of {USAGE}")
    return KINDS[kind](target)


def read_new(source: RecordSource, cursor: Mapping[str, Any]) -> tuple[list[dict[str, Any]], Cursor]:
    """Normalized spans `source` has added since `cursor` (`{}`: everything), and the next cursor.

    Built-in sources track their own positions (`read_new`). Any other source is asked for spans since
    the latest start time seen minus the longest span duration seen: spans are written when they end, so
    a parent span can arrive after its children. Spans read twice are deduplicated by the store.
    """
    own: ReadNew | None = getattr(source, "read_new", None)
    if own is not None:
        raw, next_cursor = own(cursor)
        return _normalized(source, raw), next_cursor
    spans = _normalized(source, source.spans(since=cursor.get("since")))
    if not spans:
        return spans, dict(cursor)
    lookback = max(float(cursor.get("lookback_s", 0)), *(_duration_s(s) for s in spans))
    latest = max(parse_time(s["start_time"]) for s in spans)
    return spans, {"since": iso(latest - timedelta(seconds=lookback)), "lookback_s": lookback}


def _normalized(source: RecordSource, raw: Any) -> list[dict[str, Any]]:
    try:
        return [normalize(s) for s in raw]
    except (KeyError, TypeError, ValueError) as e:
        raise SourceError(f"source {source.name!r} yielded a malformed span ({e!r})") from e


def _duration_s(span: Mapping[str, Any]) -> float:
    if not span["end_time"]:
        return 0.0
    end: datetime = parse_time(span["end_time"])
    return max((end - parse_time(span["start_time"])).total_seconds(), 0.0)
