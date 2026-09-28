import json

import pytest

import hone_lens as tl
from hone_lens.coding.review import _confirm_modes
from hone_lens.coding.sample import stratified_sample
from hone_lens.errors import HoneLensError
from hone_lens.testing import FakeEmbedder, FakeTextClient, ScriptedIO, synthetic_runs


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return synthetic_runs(
        tmp_path_factory.mktemp("syn"), n_runs=120, plant=["homogeneity_from_example", "truncation"]
    )


@pytest.fixture
def ws(tmp_path, runs) -> tl.Workspace:
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas")
    return ws


def test_stratified_sample_covers_every_stratum(ws) -> None:
    sample = stratified_sample(ws.db, "song_ideas", 12)
    ids = {r["span_id"] for r in sample}
    assert len(sample) == len(ids) == 12
    (cluster,) = ws.db.query("SELECT members FROM clusters")
    assert ids & set(json.loads(cluster["members"]))
    truncated = next(f for f in ws.findings() if f.detector == "truncation")
    assert ids & set(truncated.evidence)
    assert stratified_sample(ws.db, "song_ideas", 12) == sample  # seeded
    assert len(stratified_sample(ws.db, "song_ideas", 1000)) == 120
    assert stratified_sample(ws.db, "nothing", 5) == []


def test_confirm_modes_keep_rename_drop_merge_add() -> None:
    proposed = [tl.coding.Mode(n, n.upper()) for n in "abcdg"]
    io = ScriptedIO(["", "bee", "-", "=a", "=zzz", "e: new mode", "f", ": no name", "e: twice", ""])
    modes = _confirm_modes(io, proposed)
    assert [(m.name, m.description) for m in modes] == [
        ("a", "A; also: D"),  # d merged into a
        ("bee", "B"),
        ("g", "G"),  # merging into an unknown mode keeps it
        ("e", "new mode"),
        ("f", ""),
    ]
    assert io.shown[0] == "Proposed failure mode a: A"
    assert "no mode 'zzz' to merge g into; kept g" in io.shown


def test_unknown_label_keeps_the_suggestion(ws) -> None:
    answers = [""] * 3 + ["", "", ""] + ["nonsense", "-", ""]
    io = ScriptedIO(answers)
    taxonomy = ws.review("song_ideas", io=io, sample=3)
    assert any(s.startswith("unknown mode 'nonsense'") for s in io.shown)
    assert taxonomy.notes[1].label is None
    assert ws.db.taxonomy("song_ideas") == taxonomy


def test_review_without_ai_help_when_the_budget_is_spent(ws) -> None:
    io = ScriptedIO(["my own note", "", ""])
    taxonomy = ws.review("song_ideas", io=io, sample=2, budget="0 calls")
    assert [n.note for n in taxonomy.notes] == ["my own note", ""]
    assert taxonomy.modes == [] and "AI note: (none)" in io.shown[0]


def test_score_ranges_are_strata(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "syn", n_runs=200, plant=["score_zero_spike"])
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    sample = stratified_sample(ws.db, "song_ideas", 4)  # no clusters or findings: the rest + 3 score ranges
    zero_runs = {r["run_id"] for r in ws.db.query("SELECT run_id FROM selections WHERE value = 0")}
    assert len(sample) == 4 and {r["run_id"] for r in sample} & zero_runs


def test_review_needs_a_terminal_or_an_io(ws, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(HoneLensError, match="review needs a person at a terminal"):
        ws.review("song_ideas")
    with pytest.raises(HoneLensError, match="sample must be at least 1"):
        ws.review("song_ideas", io=ScriptedIO(), sample=0)


def test_budget_spent_during_the_agreement_check(ws) -> None:
    ws.review("song_ideas", io=ScriptedIO(), sample=8)
    report = ws.label_all("song_ideas", budget="3 calls", yes=True)
    assert report.agreement is None and report.labeled == 0 and report.budget_exhausted
    assert report.notes == ["label_all stopped: call budget of 3 spent; 120 outputs left unlabeled"]


def test_stage_4_errors(ws, tmp_path) -> None:
    with pytest.raises(HoneLensError, match="no outputs of workflow 'nothing' to review"):
        ws.review("nothing", io=ScriptedIO())
    with pytest.raises(HoneLensError, match=r"no taxonomy for 'nothing'; run review\('nothing'\) first"):
        ws.label_all("nothing", budget=None)
    bare = tl.Workspace(tmp_path / "lens")
    with pytest.raises(HoneLensError, match=r"review needs an analysis LLM; pass Workspace\(llm=...\)"):
        bare.review("song_ideas", io=ScriptedIO())
    empty = tl.Taxonomy("song_ideas", [tl.coding.Mode("x", "X")])
    report = ws.label_all("song_ideas", empty, budget=None, yes=True)
    assert report.refused.startswith("the taxonomy has no reviewed sample")


def test_console_io(monkeypatch, capsys) -> None:
    io = tl.ConsoleIO()
    io.show("hello")
    assert capsys.readouterr().out == "hello\n"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert io.ask("Proceed?", "n") == "n"  # never waits without a terminal
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "  yes ")
    assert io.ask("Proceed?") == "yes"
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert io.ask("Proceed?", "default") == "default"
