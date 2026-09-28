"""AC-1: the design/current.md §3 example (2,000 synthetic runs, fakes): a homogeneity finding with cause
`prompt_section:format_example`, confirmed by a replay test; then the HTML report."""

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, synthetic_runs

pytestmark = pytest.mark.e2e


def test_ac1_spec_example(tmp_path) -> None:
    runs = synthetic_runs(
        tmp_path,
        n_runs=2000,
        plant=["homogeneity_from_example", "truncation", "judge_equals_generator", "score_zero_spike"],
    )
    ws = tl.Workspace(
        tmp_path / "lens",
        embedder=FakeEmbedder.semantic(),
        llm=FakeTextClient.analyst(),
        replayer=FakeReplayer.removing_section_reduces_similarity(),
    )
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas")
    titles = [f.title for f in report.findings]
    assert any("share one pattern" in t and "storm" in t for t in titles)
    f = next(f for f in report.findings if f.category == "diversity")
    f = ws.explain(f.id)
    assert f.cause is not None
    assert f.cause.kind == "prompt_section" and f.cause.target == "format_example"
    assert f.cause.status == "suspected" and f.cause.effect_size and f.cause.effect_size > 0.5
    assert f.cause.ci and f.cause.ci[0] > 0.5
    assert "format_example" in f.cause.hypothesis
    t = ws.test(f.id, variants=2, samples=20)
    assert t.metric_after is not None and t.metric_before is not None
    assert t.confirmed and t.metric_after < t.metric_before
    assert t.samples == 20 and t.calls == 40 and len(t.variants) == 2
    stored = ws.finding(f.id)
    assert stored.cause and stored.cause.status == "confirmed" and stored.test == t
    assert (
        stored.fix is not None
        and stored.fix.kind == "prompt_diff"
        and "format_example" in stored.fix.description
    )
    assert "-Example: A storm opens the song" in stored.fix.diff or t.variant == "remove"


def test_ac1_replays_carry_the_finding_id(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example"])
    replayer = FakeReplayer.removing_section_reduces_similarity()
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), replayer=replayer)
    ws.ingest(f"hone:{runs.root}")
    (f,) = ws.analyze("song_ideas").findings
    ws.test(f.id, variants=1, samples=5)  # explains first
    assert len(replayer.calls) == 5
    assert all(c["trace"]["hone.lens.finding_id"] == f.id for c in replayer.calls)
    assert all(c["overrides"] == {"prompt.sections": {"format_example": None}} for c in replayer.calls)
    assert all(
        "format_example" in c["span"]["attributes"]["hone.models.prompt.sections"] for c in replayer.calls
    )
