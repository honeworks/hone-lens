"""`HoneSpanStore`: spans from honeworks span stores: SQLite stores and JSONL files,
including hone-flow run folders (`<storage>/<workflow>/runs/<run_id>/spans.jsonl`)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from hone_lens.errors import SourceError

SUPPORTED_SCHEMA_VERSIONS = ("1",)
_COLUMNS = (
    "span_id, trace_id, parent_span_id, name, kind, start_time, end_time, status_code, status_message, "
    "attributes, events, resource, links"
)


class HoneSpanStore:
    """Spans from a honeworks `spans.db` / `spans.jsonl`, or every such file below a folder: `.hone`, a
    hone-flow storage root or one workflow's `runs/` folder (every run folder's `spans.jsonl`).

    >>> source = HoneSpanStore(".hone")  # doctest: +SKIP
    >>> spans = list(source.spans(since="2026-09-01"))  # doctest: +SKIP

    Incremental reads (`read_new`) follow each SQLite store's `changes.seq` and read each JSONL file from
    where the last read stopped (files are append-only; a shorter file is read again from the start).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.name = f"hone:{self.path.resolve()}"

    def files(self) -> list[Path]:
        """The store files this source reads (raises `SourceError` when there are none)."""
        if self.path.is_file():
            return [self.path.resolve()]
        found = sorted([*self.path.rglob("spans.db"), *self.path.rglob("spans.jsonl")])
        if not found:
            raise SourceError(
                f"no honeworks span store at {self.path}; pass a spans.db or spans.jsonl file, "
                "or a folder that contains them (for example .hone)"
            )
        return [p.resolve() for p in found]

    def spans(self, *, since: str | None = None) -> list[dict[str, Any]]:
        """Every span, or those starting at or after `since` (ISO-8601)."""
        spans: list[dict[str, Any]] = []
        for path in self.files():
            if path.suffix == ".jsonl":
                spans += [s for s in _read_jsonl(path)[0] if since is None or str(s["start_time"]) >= since]
            else:
                with closing(_connect(path)) as db:
                    where, params = ("WHERE start_time >= ?", (since,)) if since else ("", ())
                    spans += _read_db(db, where, params)
        return spans

    def read_new(self, cursor: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Spans added since `cursor` (from a previous call; `{}` reads everything) and the next cursor."""
        spans: list[dict[str, Any]] = []
        marks: dict[str, Any] = {}
        for path in self.files():
            if path.suffix == ".jsonl":
                offset = cursor.get(str(path))
                # an older cursor ([mtime, size]) or a file that shrank (rewritten): read it whole
                start = offset if isinstance(offset, int) and offset <= path.stat().st_size else 0
                new, marks[str(path)] = _read_jsonl(path, start)
                spans += new
                continue
            seq = int(cursor.get(str(path), 0))
            with closing(_connect(path)) as db:
                # take the high-water mark first: spans written meanwhile are read next time
                (last,) = db.execute("SELECT coalesce(max(seq), ?) FROM changes", (seq,)).fetchone()
                where = "WHERE span_id IN (SELECT span_id FROM changes WHERE seq > ? AND seq <= ?)"
                spans += _read_db(db, where, (seq, last))
            marks[str(path)] = int(last)
        return spans, marks


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    try:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        _check_version(path, meta.get("schema_version"))
    except sqlite3.DatabaseError as e:
        db.close()
        raise SourceError(f"{path} is not a honeworks span store ({e})") from e
    except SourceError:
        db.close()
        raise
    return db


def _check_version(path: Path, version: Any) -> None:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SourceError(
            f"{path} has span schema version {version!r}; this hone-lens reads {SUPPORTED_SCHEMA_VERSIONS}. "
            "Upgrade hone-lens."
        )


def _read_db(db: sqlite3.Connection, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    rows = db.execute(f"SELECT {_COLUMNS} FROM spans {where} ORDER BY start_time", params).fetchall()  # noqa: S608 - constant SQL
    blobs: dict[str, Any] = {}
    if any('"$blob"' in row[9] for row in rows):
        blob_rows = db.execute("SELECT sha256, mime, data FROM blobs")
        blobs = {sha: _blob_value(mime, data) for sha, mime, data in blob_rows}
    return [_span(row, blobs) for row in rows]


def _blob_value(mime: str | None, data: bytes) -> Any:
    text = bytes(data).decode("utf-8", errors="replace")
    return json.loads(text) if mime == "application/json" else text


def _span(row: tuple[Any, ...], blobs: Mapping[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = json.loads(row[9])
    for key, value in attributes.items():
        if isinstance(value, dict) and "$blob" in value:
            attributes[key] = blobs.get(str(value["$blob"]), value)  # pyright: ignore[reportUnknownArgumentType]
    return {
        "span_id": row[0],
        "trace_id": row[1],
        "parent_span_id": row[2],
        "name": row[3],
        "kind": row[4],
        "start_time": row[5],
        "end_time": row[6],
        "status": {"code": row[7], "message": row[8]},
        "attributes": attributes,
        "events": json.loads(row[10]),
        "resource": json.loads(row[11]),
        "links": json.loads(row[12]),
    }


def _read_jsonl(path: Path, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """The spans on the lines after byte `offset`, and the offset after the last line read. An unfinished
    last line (no newline and not valid JSON yet: a writer is mid-line) is left for the next read."""
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    spans: list[dict[str, Any]] = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if line.strip():
            try:
                span = json.loads(line)
            except ValueError as e:
                if not line.endswith(b"\n"):
                    break
                raise SourceError(
                    f"{path}: the line at byte {offset + consumed} is not a JSON span ({e})"
                ) from e
            _check_version(path, span.get("attributes", {}).get("hone.schema_version", "1"))
            spans.append(span)
        consumed += len(line)
    return spans, offset + consumed
