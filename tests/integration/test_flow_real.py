"""The `flow` extra against the real hone-flow (installed from the sibling folder in development): run
folders written by hone-flow are read through `hone:` (spans.jsonl) and `FlowRuns` (the read API), and
`FlowStepRerunner` forks a real run."""

import json
from pathlib import Path

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns, FlowStepRerunner
from hone_lens.testing import check_record_source, check_step_rerunner

pytest.importorskip("hone_flow")
import hone_flow as fk


def _songs(storage: Path):
    wf = fk.Workflow("songs", storage=storage)

    @wf.step()
    def lyrics(topic: str, style: fk.Param[str], review_note: str | None = None) -> str:
        return f"{style} song about {topic}" + (f" ({review_note})" if review_note else "")

    @wf.gate()
    def review(lyrics: str) -> str:
        return lyrics

    @wf.step()
    def render(review: str, item: fk.Item) -> str:
        if item.id == "c":
            raise ValueError("cannot render c")
        return review.upper()

    return wf


@pytest.fixture
def flow_runs(tmp_path: Path):
    """A run with a reject-and-revise cycle, an approval, a failed step and a pause, plus a fork of it."""
    wf = _songs(tmp_path / "flows")
    items = [fk.Item(i, {"topic": t}) for i, t in (("a", "rain"), ("b", "trains"), ("c", "salt"))]
    run = wf.run(items, params={"style": "folk"})
    run.reject(step="review", item="a", note="warmer")
    run.resume()  # lyrics/a reruns with the note; the gate pauses again
    for item in ("a", "c"):
        run.approve(step="review", item=item)
    run.resume()  # render a (done) and c (fails); b still awaits review
    fork = run.fork(refresh=("render",), items=["a"])
    return wf, run, fork, tmp_path / "flows"


def test_flow_runs_reads_real_run_folders(flow_runs) -> None:
    _, run, fork, storage = flow_runs
    source = FlowRuns(str(storage), "songs")
    check_record_source(source)
    ws = tl.Workspace(storage.parent / "lens")
    ws.ingest(source)

    rows = {
        (r["run_id"], r["step"], r["item"]): r
        for r in ws.db.query("SELECT * FROM flow_steps ORDER BY run_id, step, item")
    }
    lyrics_a = rows[(run.run_id, "lyrics", "a")]
    assert (lyrics_a["status"], lyrics_a["attempt"], lyrics_a["attempts"]) == ("done", 2, 1)
    assert lyrics_a["attempt_statuses"] == '["rejected"]' and lyrics_a["review_note_present"] == 1
    gate_a = rows[(run.run_id, "review", "a")]
    assert [d["decision"] for d in json.loads(gate_a["review_decisions"])] == ["rejected", "approved"]
    assert "warmer" not in gate_a["review_decisions"]  # notes are not copied
    assert rows[(run.run_id, "review", "b")]["status"] == "awaiting_review"
    assert rows[(run.run_id, "render", "b")]["status"] == "pending"
    failed = rows[(run.run_id, "render", "c")]
    assert failed["status"] == "failed" and "cannot render c" in failed["error"]
    reused = rows[(fork.run_id, "lyrics", "a")]
    assert (reused["reused_from"], reused["fork_of"]) == (run.run_id, run.run_id)
    assert (fork.run_id, "lyrics", "b") not in rows

    steps = ws.db.query("SELECT run_id, step, item, status, executed, reused_from, fork_of FROM steps")
    statuses = {(r["run_id"], r["step"], r["item"], r["status"]) for r in steps}
    assert (run.run_id, "render", "c", "failed") in statuses
    assert any(r["reused_from"] == run.run_id and not r["executed"] for r in steps)
    assert {r["fork_of"] for r in steps if r["run_id"] == fork.run_id} == {run.run_id}
    gates = ws.db.query(
        "SELECT gate, decision FROM selections WHERE kind = 'human_gate' AND decision IS NOT NULL"
    )
    assert sorted(g["decision"] for g in gates) == ["approved", "approved", "rejected"]
    assert {g["gate"] for g in gates} == {"review"}

    # nothing changed: the next ingest asks for runs updated since the newest one and stores nothing new
    (again,) = ws.ingest(source)
    assert again.added == 0


def test_hone_source_reads_the_same_run_folders(flow_runs) -> None:
    _, run, fork, storage = flow_runs
    by_folder = tl.Workspace(storage.parent / "lens-a")
    by_folder.ingest(f"hone:{storage}")
    by_api = tl.Workspace(storage.parent / "lens-b")
    by_api.ingest(FlowRuns(str(storage), "songs"))
    sql = "SELECT span_id, run_id, step, item, status, attempt, reused_from, fork_of FROM steps ORDER BY span_id"
    assert by_folder.db.query(sql) == by_api.db.query(sql)
    assert {r["run_id"] for r in by_folder.db.query(sql)} == {run.run_id, fork.run_id}
    gate = "SELECT status FROM steps WHERE run_id = ? AND step = 'review' AND item = 'b' ORDER BY start_time"
    assert by_folder.db.query(gate, (run.run_id,))[-1]["status"] == "awaiting_review"  # a paused gate


def test_flow_step_rerunner_forks_a_real_run(flow_runs) -> None:
    wf, run, _, storage = flow_runs
    rerunner = FlowStepRerunner(wf)
    check_step_rerunner(rerunner, run.run_id, "lyrics", ["a"], {"style": "jazz"})
    (new_id,) = rerunner.rerun(run.run_id, "lyrics", ["b"], {"style": "jazz"})
    forked = fk.open_runs(storage, "songs").open_run(new_id)
    assert forked.manifest["fork_of"]["run_id"] == run.run_id
    assert [r.item for r in forked.steps("lyrics")] == ["b"]
    assert forked.output("lyrics", "b").startswith("jazz song about trains")
