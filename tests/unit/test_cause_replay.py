import pytest

import hone_lens as tl
from hone_lens.cause import _fix, rank_causes
from hone_lens.errors import HoneLensError
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, synthetic_runs


def test_rank_causes_effect_ci_and_tie_break() -> None:
    runs = {f"r{i}": {("prompt_version", "3", ""), ("prompt_section", "ex", "3")} for i in range(50)}
    runs |= {f"s{i}": {("prompt_version", "2", ""), ("model", "m", "")} for i in range(50)}
    for r in runs.values():
        r.add(("param", "temperature=0.9", ""))  # in every run: cannot separate anything
    affected = {f"r{i}" for i in range(40)}
    causes = rank_causes(runs, affected)
    top = causes[0]
    assert (top["kind"], top["target"], top["version"]) == (
        "prompt_section",
        "ex",
        "3",
    )  # a section beats a version
    assert top["effect"] == pytest.approx(0.8) and top["ci"][0] < 0.8 < top["ci"][1]
    assert (top["runs_with"], top["affected_with"]) == (50, 40)
    assert causes[1]["kind"] == "prompt_version" and causes[1]["effect"] == pytest.approx(0.8)
    assert all(c["kind"] != "param" for c in causes)
    assert causes[-1]["effect"] == pytest.approx(-0.8)


@pytest.mark.parametrize(
    ("kind", "fix_kind"),
    [
        ("prompt_section", "prompt_diff"),
        ("model", "model_swap"),
        ("param", "param"),
        ("code_version", "prompt_diff"),
    ],
)
def test_fix_kinds(kind: str, fix_kind: str) -> None:
    assert _fix({"kind": kind, "target": "x", "version": "1"}).kind == fix_kind


@pytest.fixture
def homogeneous(tmp_path):
    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example", "truncation"])

    def workspace(**ports) -> tl.Workspace:
        ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), **ports)
        ws.ingest(f"hone:{runs.root}")
        return ws

    return workspace


def test_explain_without_llm_uses_a_plain_hypothesis(homogeneous) -> None:
    ws = homogeneous()
    f = next(f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity")
    cause = ws.explain(f.id).cause
    assert cause is not None and cause.target == "format_example"
    assert cause.hypothesis.startswith("Affected runs share prompt_section format_example v3: ")


def test_explain_a_finding_without_a_cause(homogeneous) -> None:
    ws = homogeneous()
    f = next(f for f in ws.analyze("song_ideas").findings if f.detector == "truncation")
    explained = ws.explain(f.id)
    assert explained.cause is not None and explained.cause.kind == "none"
    assert "no recorded input separates the affected runs from the others" in explained.details["notes"]
    assert ws.explain(f.id).details["notes"].count(explained.cause.hypothesis) == 1  # notes are not repeated


def test_replay_needs_a_replayer_and_a_prompt_section_cause(homogeneous) -> None:
    ws = homogeneous()
    findings = {f.detector: f for f in ws.analyze("song_ideas").findings}
    with pytest.raises(
        HoneLensError, match=r"test needs a Replayer: pass Workspace\(replayer=...\) or --replayer NAME"
    ):
        ws.test(findings["homogeneity"].id)
    ws = homogeneous(replayer=FakeReplayer())
    t = ws.test(findings["truncation"].id)  # explained first: no cause
    assert not t.confirmed and t.notes == ["replay tests need a prompt_section cause in v0.1"]


def test_replay_with_rewrites_quality_and_budget(homogeneous) -> None:
    replayer = FakeReplayer.removing_section_reduces_similarity()
    llm = FakeTextClient.analyst()
    ws = homogeneous(replayer=replayer, llm=llm)
    (f,) = [f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity"]
    t = ws.test(f.id, variants=3, samples=10, quality=lambda text: 0.9 if "Captain" in text else 0.5)
    assert [v["variant"] for v in t.variants] == ["remove", "rewrite 1", "counter-instruction"]
    assert t.variants[1]["section"].startswith("Example: a baker")
    assert t.quality_before is not None and t.quality_after is not None
    assert not t.confirmed  # the metric improved, but quality fell by far more than the tolerance
    assert ws.finding(f.id).cause.status == "suspected"

    stopped = ws.test(f.id, variants=3, samples=10, budget="15 calls")  # 1 rewrite call + 10 + 4 replays
    assert stopped.calls == 15 and [v["replayed"] for v in stopped.variants] == [
        10,
        4,
    ]  # the paid-for part is kept
    assert "replay stopped: call budget of 15 spent; 4 of 10 calls of this variant replayed" in stopped.notes

    rewrite_call = next(c for c in llm.calls if c["schema"]["title"] == "section_variants")
    assert rewrite_call["trace"]["hone.lens.finding_id"] == f.id
    parts = rewrite_call["trace"]["traceparent"].split("-")
    assert [len(x) for x in parts] == [2, 32, 16, 2] and parts[0] == "00"
    replays_of_one_test = replayer.calls[-14:]
    assert len({c["trace"]["traceparent"] for c in replays_of_one_test}) == 1  # one trace per test() run


def test_failed_replays_are_notes(homogeneous) -> None:
    def broken(span, overrides):
        raise TimeoutError("model busy")

    ws = homogeneous(replayer=FakeReplayer(broken))
    (f,) = [f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity"]
    t = ws.test(f.id, variants=1, samples=3)
    assert not t.confirmed and t.metric_after is None
    assert t.notes[:3] == [
        f"replay of {s} failed: TimeoutError: model busy" for s in [n.split()[2] for n in t.notes[:3]]
    ]
    assert t.notes[-1] == "nothing was replayed; the cause stays suspected"


def test_small_feature_groups_are_not_compared_and_ci_is_honest_at_the_extremes() -> None:
    runs = {f"a{i}": {("model", "rare", "")} for i in range(4)} | {
        f"b{i}": {("model", "common", "")} for i in range(40)
    }
    assert rank_causes(runs, {f"a{i}" for i in range(4)}) == []  # 4 runs with the rare model: too few
    runs |= {"a4": {("model", "rare", "")}}
    top = rank_causes(runs, {f"a{i}" for i in range(5)})[0]
    assert top["effect"] == 1.0 and 0.4 < top["ci"][0] < 0.9  # not the zero-width Wald interval (1, 1)
    assert top["ci"][1] == pytest.approx(1.0)


def test_explain_after_a_test_keeps_the_tested_cause_and_test_is_deterministic(homogeneous) -> None:
    ws = homogeneous(replayer=FakeReplayer.removing_section_reduces_similarity())
    (f,) = [f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity"]
    first = ws.test(f.id, variants=2, samples=10)
    assert ws.explain(f.id).cause.status == "confirmed"  # type: ignore[union-attr]
    assert ws.finding(f.id).test == first
    assert ws.test(f.id, variants=2, samples=10) == first
    with pytest.raises(HoneLensError, match="variants and samples must be at least 1"):
        ws.test(f.id, samples=0)


def test_diversity_test_without_embedder_is_an_error(homogeneous, tmp_path) -> None:
    ws = homogeneous()
    (f,) = [f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity"]
    bare = tl.Workspace(tmp_path / "lens", replayer=FakeReplayer())
    with pytest.raises(HoneLensError, match="testing a diversity finding needs an embedder"):
        bare.test(f.id)


def test_replay_variant_notes_without_an_llm_and_with_a_failing_one(homogeneous) -> None:
    ws = homogeneous(replayer=FakeReplayer.removing_section_reduces_similarity())
    (f,) = [f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity"]
    t = ws.test(f.id, variants=4, samples=5)
    assert [v["variant"] for v in t.variants] == ["remove", "counter-instruction"]
    assert "no LLM to rewrite the section: 2 of 4 variants" in t.notes

    silent = homogeneous(
        replayer=FakeReplayer.removing_section_reduces_similarity(), llm=FakeTextClient([{}])
    )
    t = silent.test(f.id, variants=3, samples=5)
    assert [v["variant"] for v in t.variants] == ["remove", "counter-instruction"]
    assert any(n.startswith("the LLM wrote 0 of 1 section rewrites") for n in t.notes)


def test_replay_of_a_metric_without_a_replay_measure_is_a_note(homogeneous) -> None:
    ws = homogeneous(replayer=FakeReplayer())
    finding = next(f for f in ws.analyze("song_ideas").findings if f.detector == "homogeneity")
    ws.explain(finding.id)
    stored = ws.finding(finding.id)
    stored.metric = {**stored.metric, "name": "distinct_rate"}  # a metric replays cannot measure in v0.1
    ws.db.save_findings([stored])
    t = ws.test(finding.id, variants=1, samples=5)
    assert (t.confirmed, t.metric_before, t.notes) == (
        False,
        None,
        ["no replay measure for metric 'distinct_rate' in v0.1"],
    )
