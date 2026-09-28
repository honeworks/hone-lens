"""The workspace database schema, and conversions between span dicts and `spans` table rows."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from hone_lens.derive import COLUMNS

# hone-flow step records (the `flow` extra's `FlowRuns.step_records`): one row per (run, step, item),
# replaced whenever the run is read again. Not derived from spans, so not in `derive.COLUMNS`.
FLOW_STEPS: dict[str, str] = {
    "run_id": "TEXT NOT NULL",
    "workflow": "TEXT",
    "step": "TEXT NOT NULL",
    "item": "TEXT",  # None for a global step
    "kind": "TEXT",  # step | gate | global
    "status": "TEXT",  # done, failed, pending, skipped, blocked, awaiting_review, interrupted
    "attempt": "INTEGER",  # the current attempt number
    "attempts": "INTEGER",  # how many earlier attempts there were
    "attempt_statuses": "TEXT",  # JSON list of the earlier attempts' statuses: failed, rejected, replaced
    "reused_from": "TEXT",
    "fork_of": "TEXT",
    "run_seed": "INTEGER",  # the run's seed (manifest `seed`); step seeds (`ctx.seed`) derive from it
    "review_decisions": "TEXT",  # JSON list of {decision, actor, at, attempt} (notes left out)
    "review_note_present": "INTEGER",
    "error": "TEXT",
    "start_time": "TEXT",  # the step's start, else the run's creation time
    "duration_ms": "REAL",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,
  parent_span_id TEXT,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL,
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  status_code    TEXT NOT NULL,
  status_message TEXT NOT NULL,
  attributes     TEXT NOT NULL,
  events         TEXT NOT NULL,
  resource       TEXT NOT NULL,
  links          TEXT NOT NULL,
  source         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS findings (id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings (model TEXT NOT NULL, text_sha TEXT NOT NULL, vector BLOB NOT NULL,
                                       PRIMARY KEY (model, text_sha));
CREATE TABLE IF NOT EXISTS clusters (id TEXT PRIMARY KEY, workflow TEXT, step TEXT, rank INTEGER,
                                     size INTEGER, total INTEGER, center TEXT, members TEXT NOT NULL,
                                     description TEXT, in_common TEXT);
CREATE TABLE IF NOT EXISTS taxonomies (workflow TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS affected (finding_id TEXT NOT NULL, span_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS affected_finding ON affected(finding_id);
CREATE TABLE IF NOT EXISTS labels (span_id TEXT PRIMARY KEY, workflow TEXT, mode TEXT, source TEXT NOT NULL);
"""


def table_sql(table: str, columns: Mapping[str, str], key: str = "trace_id") -> str:
    """A derived table with an index on `key` (rows are replaced by it) and one on (workflow, step)."""
    body = ", ".join(f"{name} {kind}" for name, kind in columns.items())
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ({body});\n"
        f"CREATE INDEX IF NOT EXISTS {table}_{key} ON {table}({key});\n"
        f"CREATE INDEX IF NOT EXISTS {table}_scope ON {table}(workflow, step);\n"
    )


def create_all(db: sqlite3.Connection) -> set[str]:
    """Create every table and index (idempotent). A table made by an older hone-lens gets the columns it
    lacks (empty); returns the names of the tables that gained columns."""
    derived = "".join(table_sql(table, columns) for table, columns in COLUMNS.items())
    db.executescript(SCHEMA + derived + table_sql("flow_steps", FLOW_STEPS, key="run_id"))
    grown: set[str] = set()
    for table, columns in {**COLUMNS, "flow_steps": FLOW_STEPS}.items():
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for name in columns.keys() - have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {columns[name]}")
            grown.add(table)
    return grown


def span_row(s: Mapping[str, Any], source: str) -> tuple[Any, ...]:
    return (
        s["span_id"],
        s["trace_id"],
        s["parent_span_id"],
        s["name"],
        s["kind"],
        s["start_time"],
        s["end_time"],
        s["status"]["code"],
        s["status"]["message"],
        json.dumps(s["attributes"], default=str),
        json.dumps(s["events"], default=str),
        json.dumps(s["resource"], default=str),
        json.dumps(s["links"], default=str),
        source,
    )


SPAN_COLUMNS = (
    "span_id, trace_id, parent_span_id, name, kind, start_time, end_time, status_code, status_message, "
    "attributes, events, resource, links"
)


def full_span(row: sqlite3.Row) -> dict[str, Any]:
    """A `spans` row (selected with `SPAN_COLUMNS`) as a honeworks records span dict."""
    return {
        "span_id": row["span_id"],
        "trace_id": row["trace_id"],
        "parent_span_id": row["parent_span_id"],
        "name": row["name"],
        "kind": row["kind"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "status": {"code": row["status_code"], "message": row["status_message"]},
        "attributes": json.loads(row["attributes"]),
        "events": json.loads(row["events"]),
        "resource": json.loads(row["resource"]),
        "links": json.loads(row["links"]),
    }
