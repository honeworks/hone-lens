"""AC-17: the `flow:` source (`FlowRuns` over hone-flow's read API, here `FakeFlowRuns`): spans and step
records, `flow_steps` rows, incremental reads through `runs(updated_since=...)` that replace the rows of a
resumed run, and a clear error without the `flow` extra."""

import json
import subprocess
import sys

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.testing import FakeFlowRuns, check_record_source

pytestmark = pytest.mark.e2e

REJECTED = [{"attempt": 1, "status": "rejected", "note": "darker"}]
REVIEWS = [
    {
        "decision": "rejected",
        "actor": "ana",
        "note": "darker",
        "at": "2026-09-01T00:05:00+00:00",
        "attempt": 1,
    },
    {"decision": "approved", "actor": "ana", "note": "", "at": "2026-09-01T00:09:00+00:00", "attempt": 2},
]


def _history() -> FakeFlowRuns:
    fake = FakeFlowRuns()
    fake.add_run("r1", [{"step": "ideas", "item": "01"}, {"step": "ideas", "item": "02"}])
    fake.add_run(
        "r2",
        [
            {"step": "ideas", "item": "01", "attempt": 2, "attempts": REJECTED, "review_note": "darker"},
            {"step": "review", "item": "01", "kind": "gate", "reviews": REVIEWS, "attempt": 2},
            {"step": "ideas", "item": "02", "status": "failed", "error": {"message": "ValueError: x"}},
            {"step": "render", "item": "02", "status": "blocked"},
            {"step": "approve", "item": "01", "kind": "gate", "status": "awaiting_review"},
        ],
        status="awaiting_review",
    )
    return fake


def test_ac17_flow_runs_yields_spans_and_step_records(tmp_path) -> None:
    fake = _history()
    source = FlowRuns("s3://bucket/projects/studio", "song_ideas", history=fake)
    assert source.name == "flow:s3://bucket/projects/studio/song_ideas"
    check_record_source(source)
    ws = tl.Workspace(tmp_path / "lens")
    (result,) = ws.ingest(source)
    assert result.added == len(source.spans()) == 2 + (2 + 5) + 2  # run spans + step spans + review decisions

    rows = {(r["run_id"], r["step"], r["item"]): r for r in ws.db.query("SELECT * FROM flow_steps")}
    assert len(rows) == 7
    ideas = rows[("r2", "ideas", "01")]
    assert (ideas["status"], ideas["attempt"], ideas["attempts"]) == ("done", 2, 1)
    assert json.loads(ideas["attempt_statuses"]) == ["rejected"] and ideas["review_note_present"] == 1
    decisions = json.loads(rows[("r2", "review", "01")]["review_decisions"])
    assert [d["decision"] for d in decisions] == ["rejected", "approved"]
    assert all("note" not in d for d in decisions)  # free-text notes stay in hone-flow
    assert rows[("r2", "ideas", "02")]["error"] == "ValueError: x"
    assert rows[("r2", "render", "02")]["status"] == "blocked"
    assert (rows[("r2", "approve", "01")]["kind"], rows[("r2", "approve", "01")]["status"]) == (
        "gate",
        "awaiting_review",
    )
    assert all(r["workflow"] == "song_ideas" and r["start_time"] for r in rows.values())

    steps = ws.db.query("SELECT step, item, status FROM steps WHERE run_id = 'r2' ORDER BY step, item")
    assert [(r["step"], r["status"]) for r in steps] == [
        ("approve", "awaiting_review"),  # the gate's own span
        ("ideas", "done"),
        ("ideas", "failed"),
        ("render", "blocked"),
        ("review", "done"),
    ]
    decisions = ws.db.query(
        "SELECT gate, decision FROM selections WHERE kind = 'human_gate' ORDER BY start_time"
    )
    assert [(d["gate"], d["decision"]) for d in decisions] == [("review", "rejected"), ("review", "approved")]


def test_ac17_second_ingest_asks_updated_since_and_replaces_a_resumed_run(tmp_path) -> None:
    fake = _history()
    ws = tl.Workspace(tmp_path / "lens")
    source = FlowRuns("flows", "song_ideas", history=fake)
    ws.ingest(source)
    mark = fake.open_run("r2").updated_at
    fake.asked.clear()

    # r2 is resumed: its failed item reruns and succeeds, and its manifest's updated_at moves on
    resumed = [
        r | {"status": "done", "error": None} if r["status"] in ("failed", "blocked") else r
        for r in fake.open_run("r2").records
    ]
    fake.add_run(
        "r2",
        resumed,
        status="completed",
        created_at=fake.open_run("r2").created_at,
        updated_at="2026-09-02T00:00:00+00:00",
    )
    ws.ingest(source)
    assert fake.asked and set(fake.asked) == {mark}  # only runs updated since the high-water mark
    rows = ws.db.query("SELECT step, item, status FROM flow_steps WHERE run_id = 'r2' ORDER BY step, item")
    assert [(r["step"], r["status"]) for r in rows] == [
        ("approve", "awaiting_review"),
        ("ideas", "done"),
        ("ideas", "done"),
        ("render", "done"),
        ("review", "done"),
    ]
    assert ws.db.query("SELECT count(*) AS n FROM flow_steps") == [{"n": 7}]  # replaced, not added

    fake.asked.clear()
    (third,) = ws.ingest(source)
    assert set(fake.asked) == {"2026-09-02T00:00:00+00:00"}
    assert third.added == 0 and ws.db.query("SELECT count(*) AS n FROM flow_steps") == [{"n": 7}]


def test_ac17_one_ingest_or_several_give_the_same_rows(tmp_path) -> None:
    fake = _history()
    step_by_step = tl.Workspace(tmp_path / "a")
    step_by_step.ingest(FlowRuns("flows", "song_ideas", history=fake))
    fake.add_run("r3", [{"step": "ideas", "item": "03", "attempts": REJECTED, "attempt": 2}])
    step_by_step.ingest(FlowRuns("flows", "song_ideas", history=fake))
    at_once = tl.Workspace(tmp_path / "b")
    at_once.ingest(FlowRuns("flows", "song_ideas", history=fake))
    sql = "SELECT * FROM flow_steps ORDER BY run_id, step, item"
    assert step_by_step.db.query(sql) == at_once.db.query(sql)
    assert len(at_once.db.query(sql)) == 8


def test_ac17_flow_source_string(tmp_path) -> None:
    ws = tl.Workspace(tmp_path / "lens")
    with pytest.raises(tl.errors.SourceError, match="flow:<storage>/<workflow>"):
        ws.ingest("flow:song_ideas")


def test_ac17_without_the_flow_extra(tmp_path) -> None:
    code = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] == 'hone_flow': raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import hone_lens as tl\n"
        "from hone_lens.adapters.flow import FlowRuns\n"
        "from hone_lens.testing import FakeFlowRuns\n"
        "FlowRuns('flows', 'w', history=FakeFlowRuns())  # works without hone-flow\n"
        f"ws = tl.Workspace({str(tmp_path / 'lens')!r})\n"
        "try:\n"
        "    ws.ingest('flow:flows/song_ideas')\n"
        "except tl.errors.SourceError as e:\n"
        "    print(e)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert "hone-lens[flow]" in out.stdout


def test_ac17_review_note_text_is_not_stored(tmp_path) -> None:
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FlowRuns("flows", "song_ideas", history=_history()))
    for table in ("flow_steps", "steps", "selections", "spans"):
        rows = ws.db.query(f"SELECT * FROM {table}")
        assert rows and "darker" not in json.dumps(rows), table  # the reviewer's note stays in hone-flow
