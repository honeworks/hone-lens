"""AC-19: detectors after the hone-flow redesign: `reuse_health` finds the planted
`source_changed_version_unchanged`; `human_override` counts reject-and-revise cycles from `flow_steps`;
no detector reads `hone.flow.cache.*`."""

import inspect

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.detectors import DETECTORS
from hone_lens.testing import FakeFlowRuns, synthetic_runs

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("seed", [0, 1])
def test_ac19_reuse_health_finds_the_plant(tmp_path, seed: int) -> None:
    runs = synthetic_runs(tmp_path, n_runs=500, plant=["source_changed_version_unchanged"], seed=seed)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    (f,) = ws.analyze("song_ideas", stages=("stats",)).findings
    truth = runs.truth["source_changed_version_unchanged"]
    assert (f.detector, f.metric["name"], f.category) == ("reuse_health", "stale_source_rate", "reliability")
    assert truth["scope"].items() <= f.scope.items()
    assert f.total == 100  # the resumed runs
    assert f.affected / f.total == pytest.approx(truth["rate"], abs=0.08)
    calls = ws.db.query(
        f"SELECT warnings FROM run_calls WHERE span_id IN ({', '.join('?' * len(f.evidence))})", f.evidence
    )
    assert len(calls) == len(f.evidence) and all(
        "source_changed_version_unchanged" in c["warnings"] for c in calls
    )


def test_ac19_reuse_health_is_quiet_on_a_clean_set_with_resumed_runs(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=500)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    assert ws.db.query("SELECT count(DISTINCT run_id) AS n FROM run_calls WHERE status = 'interrupted'") == [
        {"n": 100}
    ]
    assert ws.analyze("song_ideas", stages=("stats",)).findings == []


def _revised_runs(workflow: str, n_runs: int = 10) -> FakeFlowRuns:
    """`n_runs` runs; in the first two `lyrics` was rejected twice, in the next two once."""
    fake = FakeFlowRuns()
    for n in range(n_runs):
        times = 2 if n < 2 else 1 if n < 4 else 0
        rejected = [{"attempt": a + 1, "status": "rejected"} for a in range(times)]
        fake.add_run(
            f"{workflow}-{n}",
            [
                {"step": "lyrics", "item": "01", "attempt": len(rejected) + 1, "attempts": rejected},
                {"step": "review", "item": "01", "kind": "gate"},
            ],
            workflow=workflow,
        )
    return fake


def test_ac19_human_override_counts_reject_and_revise_cycles(tmp_path) -> None:
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FlowRuns("flows", "song_ideas", history=_revised_runs("song_ideas")))
    report = ws.analyze("song_ideas", stages=("stats",))
    (f,) = report.findings
    assert (f.detector, f.metric["name"], f.scope["step"]) == ("human_override", "revision_rate", "lyrics")
    assert (f.affected, f.total, f.details["revisions"]) == (4, 10, 6)
    html = ws.report("song_ideas", fmt="html")
    (trace,) = ws.db.query("SELECT trace_id FROM spans WHERE span_id = ?", (f.evidence[0],))
    assert f'href="#trace-{trace["trace_id"]}"' in html and 'href="#trace-"' not in html


def test_ac19_revisions_are_scoped_to_the_analyzed_workflow_and_time(tmp_path) -> None:
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FlowRuns("flows", "a", history=_revised_runs("a")))
    ws.ingest(FlowRuns("flows", "b", history=_revised_runs("b", n_runs=5)))
    (f,) = ws.analyze("a", stages=("stats",)).findings
    assert (f.scope["workflow"], f.affected, f.total) == ("a", 4, 10)
    (g,) = ws.analyze("b", stages=("stats",)).findings
    assert (g.scope["workflow"], g.affected, g.total) == ("b", 4, 5)
    assert ws.analyze("a", since="2026-09-02T00:00:00Z", stages=("stats",)).findings == []


def test_ac19_no_detector_reads_the_old_cache_attributes() -> None:
    assert "cache_health" not in DETECTORS and "reuse_health" in DETECTORS
    for d in DETECTORS.values():
        assert "cache" not in inspect.getsource(d.fn)
