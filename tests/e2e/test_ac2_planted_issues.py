"""AC-2: each planted issue alone is found by the right detector with the right scope; a clean synthetic set
gives no findings (false-positive check).

The diversity plant (`homogeneity_from_example`) is found by stage 2 (output analysis), and its stats
stage must stay quiet.
"""

import json

import pytest

import hone_lens as tl
from hone_lens.testing import PLANTS, FakeEmbedder, synthetic_runs

pytestmark = pytest.mark.e2e

N_RUNS = 400
STATS_PLANTS = [p for p in PLANTS if p != "homogeneity_from_example"]


def _analyze(tmp_path, plant: list[str], seed: int = 0) -> tuple[tl.Workspace, tl.Report, dict]:
    runs = synthetic_runs(tmp_path, n_runs=N_RUNS, plant=plant, seed=seed)
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    return ws, ws.analyze("song_ideas", stages=("stats",)), runs.truth


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_ac2_clean_set_has_no_findings(tmp_path, seed: int) -> None:
    _, report, _ = _analyze(tmp_path, [], seed)
    assert report.findings == []


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("plant", STATS_PLANTS)
def test_ac2_each_plant_found_by_its_detector(tmp_path, plant: str, seed: int) -> None:
    ws, report, truth = _analyze(tmp_path, [plant], seed)
    expected = truth[plant]
    matches = [
        f
        for f in report.findings
        if f.detector == expected["detector"]
        and f.metric["name"] == expected["metric"]
        and f.category == expected["category"]
        and expected["scope"].items() <= f.scope.items()
    ]
    assert len(matches) == 1, [(f.detector, f.metric["name"], f.scope, f.title) for f in report.findings]
    (f,) = matches
    assert f.affected / f.total == pytest.approx(expected["rate"], abs=0.05 + expected["rate"] * 0.3)
    assert f.id.startswith("F-") and f.evidence
    known = {r["span_id"] for r in ws.db.query("SELECT span_id FROM spans")}
    assert set(f.evidence) <= known
    allowed = {expected["detector"], *expected["also"]}
    assert {f.detector for f in report.findings} <= allowed


def _evidence_rows(ws: tl.Workspace, f: tl.Finding, sql: str) -> list[dict]:
    marks = ", ".join("?" * len(f.evidence))
    return ws.db.query(sql.format(marks=marks), f.evidence)


def test_ac2_evidence_shows_the_planted_property(tmp_path) -> None:
    ws, report, _ = _analyze(tmp_path, ["truncation", "score_zero_spike"])
    by_metric = {f.metric["name"]: f for f in report.findings}
    truncated = by_metric["truncation_rate"]
    rows = _evidence_rows(ws, truncated, "SELECT finish_reason FROM calls WHERE span_id IN ({marks})")
    assert len(rows) == len(truncated.evidence) and {r["finish_reason"] for r in rows} == {"length"}
    assert (
        truncated.details["near_context_limit"] == truncated.affected
    )  # every planted cut is near the limit
    zeros = by_metric["zero_score_rate"]
    rows = _evidence_rows(ws, zeros, "SELECT value FROM selections WHERE span_id IN ({marks})")
    assert len(rows) == len(zeros.evidence) and {r["value"] for r in rows} == {0.0}
    assert zeros.details["failed_judge_calls"] == zeros.affected


def test_ac2_diversity_plant_is_quiet_in_stage_1(tmp_path) -> None:
    _, report, _ = _analyze(tmp_path, ["homogeneity_from_example"])
    assert report.findings == []


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_ac2_diversity_plant_found_by_output_analysis(tmp_path, seed: int) -> None:
    runs = synthetic_runs(tmp_path, n_runs=N_RUNS, plant=["homogeneity_from_example"], seed=seed)
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic())
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats", "outputs"))
    expected = runs.truth["homogeneity_from_example"]
    (f,) = report.findings
    assert (f.detector, f.category, f.metric["name"]) == ("homogeneity", "diversity", expected["metric"])
    assert expected["scope"].items() <= f.scope.items()
    assert f.affected / f.total == pytest.approx(expected["rate"], abs=0.05)
    shares = f.details["by_prompt_version"]
    assert shares["2"] < 0.05 and shares["3"] > 0.6
    assert f.metric["baseline"] == f.details["by_prompt_version"]["2"]
    top = f.details["closeness"][0]
    assert (top["section"], top["version"]) == (expected["cause"][1], "3") and top["difference"] > 0.5
    cluster_id, *spans = f.evidence
    (cluster,) = ws.db.query("SELECT size, members FROM clusters WHERE id = ?", (cluster_id,))
    assert cluster["size"] == f.affected and set(spans) <= set(json.loads(cluster["members"]))
    texts = ws.db.query(f"SELECT text FROM outputs WHERE span_id IN ({', '.join('?' * len(spans))})", spans)
    assert all("Captain" in t["text"] for t in texts)


@pytest.mark.parametrize("seed", [0, 1])
def test_ac2_clean_set_has_no_output_findings(tmp_path, seed: int) -> None:
    runs = synthetic_runs(tmp_path, n_runs=N_RUNS, seed=seed)
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic())
    ws.ingest(f"hone:{runs.root}")
    assert ws.analyze("song_ideas", stages=("stats", "outputs")).findings == []
