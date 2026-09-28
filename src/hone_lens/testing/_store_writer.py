"""Write spans into a honeworks span store (the honeworks SQLite schema) or a hone-flow run folder.
Used by the synthetic runs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spans (
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,
  parent_span_id TEXT,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'internal',
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  status_code    TEXT NOT NULL DEFAULT 'unset',
  status_message TEXT NOT NULL DEFAULT '',
  attributes     TEXT NOT NULL DEFAULT '{}',
  events         TEXT NOT NULL DEFAULT '[]',
  resource       TEXT NOT NULL DEFAULT '{}',
  links          TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS spans_name_time ON spans(name, start_time);
CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, mime TEXT, size INTEGER, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, span_id TEXT NOT NULL,
                                    op TEXT NOT NULL, at TEXT NOT NULL);
"""


def write_store(path: Path, spans: Iterable[Mapping[str, Any]], *, package: str) -> None:
    """Add new spans to the store at `path` (created with `meta` rows when missing); known ids are skipped."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.executescript(SCHEMA)
        meta = [("schema", "hone-spans"), ("schema_version", "1"), ("package", package), ("created_at", now)]
        db.executemany("INSERT OR IGNORE INTO meta VALUES (?, ?)", meta)
        known = {r[0] for r in db.execute("SELECT span_id FROM spans")}
        rows = [_row(s) for s in spans if s["span_id"] not in known]
        db.executemany("INSERT INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        changes = [(r[0], now) for r in rows]
        db.executemany("INSERT INTO changes (span_id, op, at) VALUES (?, 'insert', ?)", changes)
    db.close()


def _row(s: Mapping[str, Any]) -> tuple[Any, ...]:
    status: Mapping[str, Any] = s.get("status") or {}
    return (
        s["span_id"],
        s["trace_id"],
        s.get("parent_span_id"),
        s["name"],
        s.get("kind", "internal"),
        s["start_time"],
        s.get("end_time"),
        status.get("code", "unset"),
        status.get("message", ""),
        json.dumps(s.get("attributes", {})),
        json.dumps(s.get("events", [])),
        json.dumps(s.get("resource", {})),
        json.dumps(s.get("links", [])),
    )


def write_run_folder(folder: Path, spans: Sequence[Mapping[str, Any]]) -> None:
    """A hone-flow run folder with `spans.jsonl` and a minimal `manifest.json` (hone-flow's run format
    `format_version: "1"` has many more keys; hone-lens' `hone:` source reads only the spans)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spans.jsonl").write_text("".join(json.dumps(s, sort_keys=True) + "\n" for s in spans))
    calls = [s for s in spans if s["name"] == "hone.flow.run"]  # one per run / resume call
    run = calls[-1]
    attributes = run["attributes"]
    manifest = {
        "format_version": "1",
        "run_id": attributes["hone.run_id"],
        "workflow": attributes["hone.flow.workflow"],
        "workflow_version": attributes["hone.flow.workflow_version"],
        "created_at": min(s["start_time"] for s in spans),
        "updated_at": max(s["end_time"] for s in spans),
        "status": attributes["hone.flow.status"],
        "trace_id": run["trace_id"],
        "fork_of": None,
        "pinned": False,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
