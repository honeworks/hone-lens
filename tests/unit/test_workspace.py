import sqlite3

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.errors import SourceError
from hone_lens.testing import FakeFlowRuns, FakeRecordSource, synthetic_runs
from hone_lens.testing.contracts import example_call_span


def _count(ws: tl.Workspace, table: str) -> int:
    return ws.db.query(f"SELECT count(*) AS n FROM {table}")[0]["n"]


def test_ingest_source_objects_and_strings(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "syn", n_runs=10)
    ws = tl.Workspace(tmp_path / "lens")
    (hone,) = ws.ingest(tl.sources.HoneSpanStore(runs.root))
    assert (hone.read, hone.added) == (72, 72)  # 7 spans a run + 2 resume calls
    (otlp,) = ws.ingest(f"otlp:{runs.otlp_path}")
    assert (otlp.read, otlp.added) == (20, 20)
    assert _count(ws, "spans") == 92
    assert _count(ws, "outputs") == 20
    assert (tmp_path / "lens" / "lens.db").exists()


def test_workspace_keeps_the_ports(tmp_path) -> None:
    llm, embedder = object(), object()
    ws = tl.Workspace(tmp_path, llm=llm, embedder=embedder)  # type: ignore[arg-type]
    assert ws.llm is llm and ws.embedder is embedder and ws.replayer is None


def test_generic_source_is_deduplicated_by_span_id(tmp_path) -> None:
    span = example_call_span()
    ws = tl.Workspace(tmp_path)
    source = FakeRecordSource([span])
    assert ws.ingest(source) == [tl.Ingested("fake", 1, 1)]
    assert ws.ingest(source) == [tl.Ingested("fake", 1, 0)]  # re-read at the time boundary, not stored twice
    other = FakeRecordSource([span], name="other")
    assert ws.ingest(other) == [tl.Ingested("other", 1, 0)]
    assert _count(ws, "spans") == 1 and _count(ws, "calls") == 1


def test_state_survives_reopening(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "syn", n_runs=5)
    tl.Workspace(tmp_path / "lens").ingest(f"hone:{runs.root}")
    ws = tl.Workspace(tmp_path / "lens")
    assert ws.ingest(f"hone:{runs.root}")[0].read == 0
    assert _count(ws, "calls") == 10


def test_malformed_span_is_a_source_error_and_nothing_is_stored(tmp_path) -> None:
    bad = {**example_call_span(), "start_time": "not a time"}
    ws = tl.Workspace(tmp_path)
    with pytest.raises(SourceError, match="malformed span"):
        ws.ingest(FakeRecordSource([example_call_span(), bad]))
    assert _count(ws, "spans") == 0 and ws.db.cursor("fake") == {}


def test_unknown_source_string(tmp_path) -> None:
    with pytest.raises(SourceError, match="unknown source"):
        tl.Workspace(tmp_path).ingest("s3:bucket")


def test_late_flow_spans_update_calls_from_an_earlier_ingest(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "syn", n_runs=3)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root / 'models'}")
    assert {r["workflow"] for r in ws.db.query("SELECT workflow FROM calls")} == {"oneshotstudio"}
    ws.ingest(f"hone:{runs.flows}")
    assert {r["workflow"] for r in ws.db.query("SELECT workflow FROM calls")} == {"song_ideas"}
    assert _count(ws, "calls") == 6  # rebuilt, not duplicated


def test_query_params(tmp_path) -> None:
    ws = tl.Workspace(tmp_path)
    ws.ingest(FakeRecordSource([example_call_span()]))
    assert ws.db.query("SELECT model FROM calls WHERE span_id = ?", (example_call_span()["span_id"],)) == [
        {"model": "gemma4-12b:latest"}
    ]
    assert ws.db.query("SELECT count(*) AS n FROM calls WHERE model = :m", {"m": "x"}) == [{"n": 0}]
    ws.db.close()


def test_ingest_is_atomic_when_step_records_fail(tmp_path) -> None:
    fake = FakeFlowRuns()
    fake.add_run("r1", [{"step": "ideas", "item": "01", "status": "failed"}])

    class Broken(FlowRuns):
        broken = False

        def step_records(self, *, since=None):
            rows = super().step_records(since=since)
            return [{**r, "run_id": None} for r in rows] if self.broken else rows

    source = Broken("flows", "song_ideas", history=fake)
    ws = tl.Workspace(tmp_path)
    ws.ingest(source)
    before = (_count(ws, "spans"), ws.db.query("SELECT * FROM flow_steps"), ws.db.cursor(source.name))
    fake.add_run("r1", [{"step": "ideas", "item": "01"}], updated_at="2026-09-02T00:00:00+00:00")
    fake.add_run("r2", [{"step": "ideas", "item": "02"}], updated_at="2026-09-02T00:00:00+00:00")
    source.broken = True
    with pytest.raises(sqlite3.IntegrityError):
        ws.ingest(source)
    after = (_count(ws, "spans"), ws.db.query("SELECT * FROM flow_steps"), ws.db.cursor(source.name))
    assert after == before  # spans, step records and the cursor: all or nothing
    source.broken = False
    ws.ingest(source)
    rows = ws.db.query("SELECT run_id, status FROM flow_steps ORDER BY run_id")
    assert rows == [{"run_id": "r1", "status": "done"}, {"run_id": "r2", "status": "done"}]


def test_a_workspace_from_an_older_version_gains_the_new_columns(tmp_path) -> None:
    fake = FakeFlowRuns()
    fake.add_run("r1", [{"step": "ideas", "item": "01"}], seed=5)
    runs = synthetic_runs(tmp_path / "syn", n_runs=2)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FlowRuns("flows", "song_ideas", history=fake), f"hone:{runs.root}")
    ws.db.close()
    with sqlite3.connect(tmp_path / "lens" / "lens.db") as old:  # the tables as an older hone-lens made them
        old.execute("ALTER TABLE flow_steps DROP COLUMN run_seed")
        old.execute("ALTER TABLE run_calls DROP COLUMN seed")
        old.execute("DELETE FROM calls")
    old.close()

    ws = tl.Workspace(tmp_path / "lens")
    assert _count(ws, "calls") == 4  # derived tables are rebuilt from the stored spans
    assert ws.db.cursor("flow:flows/song_ideas") == {}  # step records are read again on the next ingest
    assert ws.db.cursor(f"hone:{runs.root}") != {}
    assert ws.db.query("SELECT run_seed FROM flow_steps") == [{"run_seed": None}]
    ws.ingest(FlowRuns("flows", "song_ideas", history=fake))
    assert ws.db.query("SELECT run_seed FROM flow_steps") == [{"run_seed": 5}]
