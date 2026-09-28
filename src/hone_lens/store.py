"""`AnalyticsDB`: the workspace's SQLite database of ingested spans and the derived analytics tables.

Tables: `spans` (span-store columns plus `source`), `calls`, `steps`, `selections`, `outputs`,
`run_calls` (columns in `derive.COLUMNS`), `flow_steps` (hone-flow step records, `schema.FLOW_STEPS`),
`cursors` (per-source high-water marks for incremental ingest) and `findings` (every finding ever
reported, by id, with its status).
"""

from __future__ import annotations

import itertools
import json
import sqlite3
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import TypeAdapter

from hone_lens.coding.taxonomy import Taxonomy
from hone_lens.derive import COLUMNS, derive
from hone_lens.findings import Finding
from hone_lens.schema import FLOW_STEPS, SPAN_COLUMNS, create_all, full_span, span_row

_FINDING = TypeAdapter(Finding)
_TAXONOMY = TypeAdapter(Taxonomy)
SCOPED_TABLES = (*COLUMNS, "flow_steps")


class AnalyticsDB:
    """The analytics tables of a workspace. Custom detectors read them with `query`::

    rows = db.query("SELECT step, avg(latency_ms) AS ms FROM calls WHERE workflow = ? GROUP BY step",
                    ("song_ideas",))
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._upgrade(create_all(self._db))

    def _upgrade(self, grown: set[str]) -> None:
        """Fill the columns an older hone-lens did not have: derived tables are rebuilt from the stored
        spans; `flow_steps` rows come back when the `flow:` sources are read again (their cursors are
        cleared, known spans are still skipped)."""
        with self._db:
            if grown & set(COLUMNS):
                self._rebuild([row[0] for row in self._db.execute("SELECT DISTINCT trace_id FROM spans")])
            if "flow_steps" in grown:
                self._db.execute("DELETE FROM cursors WHERE source LIKE 'flow:%'")

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[dict[str, Any]]:
        """Rows of a read query as dicts."""
        return [dict(row) for row in self._db.execute(sql, params)]

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def scoped(self, workflow: str | None = None, since: str | None = None) -> Generator[AnalyticsDB]:
        """Inside the block, the derived tables and `flow_steps` hold only the rows of `workflow` starting at
        or after `since` (temporary tables shadow the full ones), so every detector, built-in or custom,
        sees the same slice through plain table names."""
        if workflow is None and since is None:
            yield self
            return
        try:
            for table in SCOPED_TABLES:
                self._db.execute(
                    f"CREATE TEMP TABLE {table} AS SELECT * FROM main.{table} "  # noqa: S608 - constant names
                    "WHERE (?1 IS NULL OR workflow = ?1) AND (?2 IS NULL OR start_time >= ?2)",
                    (workflow, since),
                )
            yield self
        finally:
            for table in SCOPED_TABLES:
                self._db.execute(f"DROP TABLE IF EXISTS temp.{table}")

    def vectors(self, model: str, shas: Iterable[str]) -> dict[str, NDArray[np.float32]]:
        """Cached embeddings of `model` for the given text hashes (those not cached are left out)."""
        self._fill_temp("wanted", shas)
        rows = self._db.execute(
            "SELECT text_sha, vector FROM embeddings "
            "WHERE model = ? AND text_sha IN (SELECT id FROM temp.wanted)",
            (model,),
        )
        return {sha: np.frombuffer(blob, dtype=np.float32) for sha, blob in rows}  # pyright: ignore[reportUnknownMemberType]

    def save_vectors(self, model: str, vectors: Mapping[str, NDArray[np.float32]]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)",
                ((model, sha, v.astype(np.float32).tobytes()) for sha, v in vectors.items()),
            )

    def save_clusters(
        self, workflow: str | None, step: str | None, clusters: Sequence[Mapping[str, Any]]
    ) -> None:
        """Replace the clusters of (workflow, step); a cluster that keeps its id keeps its description."""
        with self._db:
            self._db.executemany(
                "INSERT INTO clusters (id, workflow, step, rank, size, total, center, members) "
                "VALUES (:id, :workflow, :step, :rank, :size, :total, :center, :members) "
                "ON CONFLICT(id) DO UPDATE SET rank = excluded.rank, size = excluded.size, "
                "total = excluded.total, members = excluded.members",
                ({**c, "members": json.dumps(c["members"])} for c in clusters),
            )
            keep = [c["id"] for c in clusters]
            marks = ", ".join("?" * len(keep))
            self._db.execute(
                f"DELETE FROM clusters WHERE workflow IS ? AND step IS ? AND id NOT IN ({marks})",  # noqa: S608
                (workflow, step, *keep),
            )

    def describe_cluster(self, cluster_id: str, description: str, in_common: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE clusters SET description = ?, in_common = ? WHERE id = ?",
                (description, in_common, cluster_id),
            )

    def save_taxonomy(self, taxonomy: Taxonomy) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO taxonomies VALUES (?, ?)",
                (taxonomy.workflow or "", _TAXONOMY.dump_json(taxonomy).decode()),
            )

    def taxonomy(self, workflow: str | None) -> Taxonomy | None:
        """The taxonomy last confirmed for `workflow`, if any."""
        row = self._db.execute("SELECT data FROM taxonomies WHERE workflow = ?", (workflow or "",)).fetchone()
        return _TAXONOMY.validate_json(row[0]) if row else None

    def save_labels(self, workflow: str | None, labels: Mapping[str, tuple[str | None, str]]) -> None:
        """Store span id -> (failure mode or None, `human` / `llm`)."""
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO labels VALUES (?, ?, ?, ?)",
                ((span_id, workflow, mode, source) for span_id, (mode, source) in labels.items()),
            )

    def findings(self) -> list[Finding]:
        """Every stored finding, by id."""
        rows = self._db.execute("SELECT data FROM findings ORDER BY id")
        return [_FINDING.validate_json(data) for (data,) in rows]

    def save_findings(self, findings: Iterable[Finding]) -> None:
        """Insert or update findings (by id); their `affected_ids`, when given, replace the stored ones."""
        with self._db:
            for f in findings:
                data = _FINDING.dump_json(f, exclude={"affected_ids"}).decode()
                self._db.execute("INSERT OR REPLACE INTO findings VALUES (?, ?, ?)", (f.id, f.key, data))
                if f.affected_ids:
                    self._db.execute("DELETE FROM affected WHERE finding_id = ?", (f.id,))
                    self._db.executemany(
                        "INSERT INTO affected VALUES (?, ?)", ((f.id, s) for s in f.affected_ids)
                    )

    def spans(self, span_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Stored spans (honeworks records shape) with these ids, in the order given."""
        self._fill_temp("wanted", span_ids)
        wanted = "SELECT id FROM temp.wanted"
        rows = self._db.execute(f"SELECT {SPAN_COLUMNS} FROM spans WHERE span_id IN ({wanted})")  # noqa: S608
        by_id = {row["span_id"]: full_span(row) for row in rows}
        return [by_id[s] for s in span_ids if s in by_id]

    def output_texts(self, span_ids: Iterable[str], max_chars: int = 600) -> list[str]:
        """Output texts (cut to `max_chars`) of these output span ids, oldest first."""
        self._fill_temp("wanted", span_ids)
        rows = self._db.execute(
            "SELECT text FROM outputs WHERE span_id IN (SELECT id FROM temp.wanted) "
            "ORDER BY start_time, span_id"
        )
        return [text[:max_chars] for (text,) in rows]

    def run_ids(self, span_ids: Iterable[str]) -> set[str]:
        """The runs these call / step / selection span ids belong to."""
        self._fill_temp("wanted", span_ids)
        runs: set[str] = set()
        for table in ("calls", "steps", "selections"):
            sql = f"SELECT run_id FROM {table} WHERE span_id IN (SELECT id FROM temp.wanted)"  # noqa: S608
            rows = self._db.execute(sql)
            runs |= {run for (run,) in rows}
        return runs

    def traces_of(self, span_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        """trace id -> every stored span of that trace, for the traces these spans belong to."""
        self._fill_temp("wanted", span_ids)
        rows = self._db.execute(
            f"SELECT {SPAN_COLUMNS} FROM spans WHERE trace_id IN "  # noqa: S608 - constant columns
            "(SELECT trace_id FROM spans WHERE span_id IN (SELECT id FROM temp.wanted)) "
            "ORDER BY trace_id, start_time"
        )
        traces: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            traces.setdefault(row["trace_id"], []).append(full_span(row))
        return traces

    def affected(self, finding_id: str) -> list[str]:
        """Every span id a finding was about when it was last found."""
        rows = self._db.execute(
            "SELECT span_id FROM affected WHERE finding_id = ? ORDER BY rowid", (finding_id,)
        )
        return [span_id for (span_id,) in rows]

    def cursor(self, source: str) -> dict[str, Any]:
        """The incremental-read position stored for `source` (`{}` before its first ingest)."""
        row = self._db.execute("SELECT value FROM cursors WHERE source = ?", (source,)).fetchone()
        return json.loads(row[0]) if row else {}

    def add(
        self,
        source: str,
        spans: Sequence[Mapping[str, Any]],
        cursor: Mapping[str, Any],
        step_records: Sequence[Mapping[str, Any]] = (),
    ) -> int:
        """Store normalized spans (known span ids are skipped), rebuild the derived rows of their traces,
        replace the `flow_steps` rows of the runs in `step_records` and save `cursor`, all in one
        transaction. Returns how many spans were new."""
        with self._db:
            added = self._db.executemany(
                "INSERT OR IGNORE INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (span_row(s, source) for s in spans),
            ).rowcount
            if added > 0:
                self._rebuild({s["trace_id"] for s in spans})
            if step_records:
                self._replace_flow_steps(step_records)
            self._db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?, ?)", (source, json.dumps(cursor, sort_keys=True))
            )
        return max(added, 0)

    def _replace_flow_steps(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._fill_temp("todo", {str(r["run_id"]) for r in records})
        self._db.execute("DELETE FROM flow_steps WHERE run_id IN (SELECT id FROM temp.todo)")
        names = ", ".join(FLOW_STEPS)
        marks = ", ".join(f":{c}" for c in FLOW_STEPS)
        rows = [{c: r.get(c) for c in FLOW_STEPS} for r in records]
        self._db.executemany(f"INSERT INTO flow_steps ({names}) VALUES ({marks})", rows)  # noqa: S608

    def _fill_temp(self, table: str, values: Iterable[str]) -> None:
        """A temporary one-column table `temp.<table>(id)` holding `values`, for `IN (SELECT id ...)`."""
        self._db.execute(f"CREATE TEMP TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY)")
        self._db.execute(f"DELETE FROM temp.{table}")  # noqa: S608 - constant names
        self._db.executemany(f"INSERT OR IGNORE INTO temp.{table} VALUES (?)", ((v,) for v in values))  # noqa: S608

    def _rebuild(self, trace_ids: Iterable[str]) -> None:
        self._fill_temp("todo", trace_ids)
        for table in COLUMNS:
            self._db.execute(f"DELETE FROM {table} WHERE trace_id IN (SELECT id FROM temp.todo)")  # noqa: S608 - table names are constants
        rows = self._db.execute(
            f"SELECT {SPAN_COLUMNS} FROM spans "  # noqa: S608 - constant columns
            "WHERE trace_id IN (SELECT id FROM temp.todo) ORDER BY trace_id, start_time, span_id"
        )
        derived: dict[str, list[dict[str, Any]]] = {table: [] for table in COLUMNS}
        for _, trace in itertools.groupby(map(full_span, rows), key=lambda s: s["trace_id"]):
            for table, table_rows in derive(list(trace)).items():
                derived[table] += table_rows
        for table, columns in COLUMNS.items():
            names = ", ".join(columns)
            marks = ", ".join(f":{c}" for c in columns)
            self._db.executemany(f"INSERT INTO {table} ({names}) VALUES ({marks})", derived[table])  # noqa: S608
