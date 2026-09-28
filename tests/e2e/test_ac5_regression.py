"""AC-5: a latency regression after prompt v4 is flagged with the version boundary."""

import pytest

import hone_lens as tl
from hone_lens.testing import synthetic_runs

pytestmark = pytest.mark.e2e


def test_ac5_latency_regression_after_prompt_v4(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=400, plant=["latency_regression_after_v4"])
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats",))

    (f,) = [f for f in report.findings if f.detector == "regression"]
    assert f.category == "regression" and f.severity == "high"
    assert f.scope["prompt_version"] == "4" and f.scope["step"] == "ideas"
    boundary = f.details["boundary"]
    assert (boundary["field"], boundary["from"], boundary["to"]) == ("prompt_version", "3", "4")
    first_v4 = ws.db.query("SELECT min(start_time) AS t FROM calls WHERE template_version = '4'")[0]["t"]
    assert boundary["at"] == first_v4
    assert f.metric["name"] == "latency_ms_median"
    assert f.metric["value"] > 1.5 * f.metric["baseline"] and f.metric["p_value"] < 0.01
    assert f.affected == 100 and f.total == 400 and "v3 -> v4" in f.title
    assert f.scope["time_range"][0] == boundary["at"]
    marks = ", ".join("?" * len(f.evidence))
    versions = ws.db.query(f"SELECT template_version FROM calls WHERE span_id IN ({marks})", f.evidence)
    assert len(versions) == len(f.evidence) and {v["template_version"] for v in versions} == {"4"}
    assert f.id in ws.report("song_ideas")


def test_ac5_no_regression_at_an_unchanged_boundary(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=400)  # v2 -> v3 without any latency change
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    assert [f for f in ws.analyze(stages=("stats",)).findings if f.detector == "regression"] == []
