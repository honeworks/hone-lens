"""AC-8: a dismissed finding stays dismissed on re-analysis; a fixed finding is re-checked."""

import pytest

import hone_lens as tl
from hone_lens.testing import synthetic_runs

pytestmark = pytest.mark.e2e


def _ids(report: tl.Report) -> dict[str, str]:
    return {f.detector: f.id for f in report.findings}


def test_ac8_dismissed_stays_dismissed_and_fixed_is_rechecked(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=300, plant=["truncation", "vision_400"], seed=0)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    first = _ids(ws.analyze("song_ideas", stages=("stats",)))
    ws.set_status(first["truncation"], "fixed", note="raised the context limit")
    capability = next(f for f in ws.findings() if f.metric["name"] == "capability_error_rate")
    ws.set_status(capability.id, "dismissed", note="cover art scoring is being removed")

    again = ws.analyze("song_ideas", stages=("stats",))
    assert capability.id not in [f.id for f in again.findings]
    assert ws.finding(capability.id).status == "dismissed"
    assert ws.finding(first["truncation"]).status == "fixed"  # found again, but only in runs before the fix

    synthetic_runs(tmp_path, n_runs=100, plant=["vision_400"], seed=1)  # new runs: no truncation
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas", stages=("stats",))
    assert ws.finding(first["truncation"]).status == "fixed"
    assert ws.finding(capability.id).status == "dismissed"  # still happening, still dismissed

    synthetic_runs(tmp_path, n_runs=100, plant=["truncation"], seed=2)  # the problem is back
    ws.ingest(f"hone:{runs.root}")
    later = ws.analyze("song_ideas", stages=("stats",))
    truncation = ws.finding(first["truncation"])
    assert truncation.status == "regressed" and truncation.details["note"] == "raised the context limit"
    assert truncation.id in [f.id for f in later.findings]
    assert [f.id for f in ws.findings(status="regressed")] == [truncation.id]
