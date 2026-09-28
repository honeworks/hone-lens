"""AC-16: hone-flow run folders through `hone:<folder>`: spans from every `<run>/spans.jsonl`; step
statuses from `hone.flow.status`; attempt / reuse / fork provenance; incremental reads of new and grown
files; nothing reads hone-flow's old `.hone/flow/spans.db` metadata or cache attributes."""

import json
from pathlib import Path

import pytest

import hone_lens as tl
from hone_lens.testing import FakeFlowRuns, synthetic_runs

pytestmark = pytest.mark.e2e


def _write_run_folder(storage: Path, run) -> Path:
    folder = storage / run.workflow / "runs" / run.run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(run.manifest))
    (folder / "spans.jsonl").write_text("".join(json.dumps(s) + "\n" for s in run.spans()))
    return folder / "spans.jsonl"


def _review_runs() -> FakeFlowRuns:
    fake = FakeFlowRuns()
    fake.add_run(
        "r1",
        [
            {"step": "lyrics", "item": "a", "attempt": 2, "attempts": [{"attempt": 1, "status": "rejected"}]},
            *({"step": "lyrics", "item": i, "status": "failed"} for i in "bcd"),
            *(
                {"step": "lyrics", "item": i, "status": s}
                for i, s in zip("efg", ("skipped", "blocked", "pending"), strict=True)
            ),
            {"step": "review", "item": "a", "kind": "gate", "status": "awaiting_review"},
            {"step": "review", "item": "b", "kind": "gate", "status": "blocked"},
            {"step": "render", "item": "a", "status": "skipped"},
        ],
        status="awaiting_review",
    )
    fake.open_run("r1").fork(refresh=("render",), items=["a"])
    return fake


def test_ac16_run_folder_spans_statuses_and_provenance(tmp_path: Path) -> None:
    fake = _review_runs()
    for run in fake.runs():
        _write_run_folder(tmp_path / "flows", run)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{tmp_path / 'flows' / 'song_ideas' / 'runs'}")  # one workflow's runs/ folder
    steps = {
        (r["run_id"], r["step"], r["item"]): r
        for r in ws.db.query(
            "SELECT run_id, step, item, status, executed, attempt, reused_from, fork_of FROM steps"
        )
    }
    assert steps[("r1", "lyrics", "a")]["attempt"] == 2 and steps[("r1", "lyrics", "a")]["status"] == "done"
    assert steps[("r1", "lyrics", "b")]["status"] == "failed"
    assert (
        steps[("r1", "render", "a")]["status"] == "skipped" and not steps[("r1", "render", "a")]["executed"]
    )
    told_apart = {k[1:]: (r["status"], r["executed"]) for k, r in steps.items() if k[0] == "r1"}
    assert {k: told_apart[k] for k in [("lyrics", "e"), ("lyrics", "f"), ("lyrics", "g")]} == {
        ("lyrics", "e"): ("skipped", 0),
        ("lyrics", "f"): ("blocked", 0),
        ("lyrics", "g"): ("pending", 0),
    }
    # a gate's own span is a step too: a paused or blocked gate is told apart from a done one
    assert told_apart[("review", "a")] == ("awaiting_review", 0)
    assert told_apart[("review", "b")] == ("blocked", 0)
    calls = {r["run_id"]: (r["status"], r["fork_of"]) for r in ws.db.query("SELECT * FROM run_calls")}
    assert calls == {"r1": ("awaiting_review", None), "r1-fork-1": ("completed", "r1")}
    fork = steps[("r1-fork-1", "lyrics", "a")]
    assert (fork["reused_from"], fork["fork_of"], fork["executed"]) == ("r1", "r1", 0)
    assert steps[("r1-fork-1", "render", "a")]["executed"] == 1
    assert ws.db.query("SELECT count(*) AS n FROM selections") == [{"n": 0}]  # no review decisions yet

    # skipped, blocked, pending and fork-copied steps are neither failures nor successes: 3 of 4 failed
    report = ws.analyze("song_ideas", stages=("stats",))
    (failures,) = [f for f in report.findings if f.metric["name"] == "step_error_rate"]
    assert (failures.affected, failures.total, failures.scope["step"]) == (3, 4, "lyrics")


def test_ac16_synthetic_run_folders_and_incremental_reads(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=50)
    ws = tl.Workspace(tmp_path / "lens")
    (first,) = ws.ingest(f"hone:{runs.flows}")
    assert first.added == 50 * 2 + 10  # run + step span per run, and 10 resume calls
    assert ws.db.query("SELECT count(*) AS n FROM steps WHERE status = 'done'") == [{"n": 50}]
    assert ws.db.query("SELECT count(*) AS n FROM run_calls") == [{"n": 60}]

    (again,) = ws.ingest(f"hone:{runs.flows}")
    assert again.read == 0  # nothing new: no file read again

    grown = runs.flows / "song_ideas" / "runs" / "run-0-00000" / "spans.jsonl"
    call = json.loads(grown.read_text().splitlines()[0]) | {
        "span_id": "f" * 16,
        "start_time": "2026-08-02T00:00:00Z",
    }
    with grown.open("a") as f:  # a resume call appends to the run's spans.jsonl
        f.write(json.dumps(call) + "\n")
    new = _write_run_folder(runs.flows, FakeFlowRuns().add_run("new-run", [{"step": "ideas", "item": "x"}]))
    (third,) = ws.ingest(f"hone:{runs.flows}")
    assert (third.read, third.added) == (1 + len(new.read_text().splitlines()), third.read)


def test_ac16_no_reader_of_the_old_flow_store_remains() -> None:
    src = Path(__file__).parents[2] / "src" / "hone_lens"
    code = "\n".join(p.read_text() for p in src.rglob("*.py"))
    for old in ("hone.flow.cache", "cache_hit", "cache_reason", "flow/spans.db", "cache_health"):
        assert old not in code, old
