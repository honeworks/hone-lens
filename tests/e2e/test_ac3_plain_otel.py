"""AC-3: plain OTel GenAI traces (no hone.* fields) from OTLP JSON.

Stats and output findings work; the section-level cause is unavailable and the finding says so (stage 5
adds the cause ranking).
"""

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, synthetic_runs

pytestmark = pytest.mark.e2e


def test_ac3_plain_otel_ingest(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example", "truncation"])
    ws = tl.Workspace(tmp_path / "lens")
    (result,) = ws.ingest(f"otlp:{runs.otlp_path.parent}/*.json")
    assert result.added == 400  # one step span and one chat span per run

    attributes = ws.db.query("SELECT attributes FROM spans")
    assert not any('"hone.' in row["attributes"] for row in attributes), "the fixture must be plain OTel"

    calls = ws.db.query("SELECT * FROM calls")
    assert len(calls) == 200
    assert {(c["workflow"], c["step"]) for c in calls} == {("song_ideas", "generate_idea")}
    assert all(
        c["provider"] == "ollama" and c["input_tokens"] > 0 for c in calls
    )  # older gen_ai names mapped
    assert {c["finish_reason"] for c in calls} == {"stop", "length"}
    assert all(c["sections"] is None and c["template_version"] is None for c in calls)

    outputs = ws.db.query("SELECT text FROM outputs WHERE workflow = 'song_ideas'")
    assert len(outputs) == 200
    assert sum("Captain" in o["text"] for o in outputs) > 40  # the planted pattern is in the texts


def test_ac3_plain_otel_stats_findings(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=400, plant=["truncation"])
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"otlp:{runs.otlp_path}")
    report = ws.analyze("song_ideas", stages=("stats",))
    (f,) = report.findings
    assert f.detector == "truncation" and f.scope["step"] == "generate_idea"
    assert 0.02 < f.affected / f.total < 0.08
    assert f.details["near_context_limit"] == 0  # plain OTel records no context limit


def test_ac3_plain_otel_output_findings_say_section_cause_unavailable(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=400, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic())
    ws.ingest(f"otlp:{runs.otlp_path}")
    (f,) = ws.analyze("song_ideas").findings
    assert f.detector == "homogeneity" and f.scope["step"] == "generate_idea"
    assert f.affected / f.total == pytest.approx(0.4, abs=0.05)
    assert f.details["closeness"] == []
    assert any("section-level cause is unavailable" in note for note in f.details["notes"])
    explained = ws.explain(f.id)
    assert explained.cause is not None and explained.cause.kind != "prompt_section"
    assert any("section-level cause is unavailable" in note for note in explained.details["notes"])
