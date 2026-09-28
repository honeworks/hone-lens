"""AC-7: review with scripted IO: notes accepted / edited, taxonomy confirmed, agreement computed, labels
counted."""

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, ScriptedIO, synthetic_runs

pytestmark = pytest.mark.e2e


@pytest.fixture
def ws(tmp_path) -> tl.Workspace:
    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas")
    return ws


def test_ac7_review_confirm_taxonomy_and_label_all(ws) -> None:
    answers = ["too generic, no concrete image"] + [""] * 9  # edit the first note, accept the others
    answers += ["copies_example", "-"]  # rename the storm mode, drop the one made from the edited note
    answers += ["vague: the idea lacks concrete details", ""]  # add a mode, done
    answers += ["vague"] + [""] * 9  # label the first output by hand, accept the AI's suggestions
    io = ScriptedIO(answers)
    taxonomy = ws.review("song_ideas", io=io, sample=10)

    assert len(taxonomy.notes) == 10 and io.answers == []
    edited = [n for n in taxonomy.notes if n.edited]
    assert [n.note for n in edited] == ["too generic, no concrete image"]
    assert all("AI note: " in shown for shown in io.shown if shown.startswith("--- output"))
    assert [m.name for m in taxonomy.modes] == ["copies_example", "vague"]
    storm = [n for n in taxonomy.notes if "Captain" in n.output]
    assert storm and {n.label for n in storm if not n.edited} == {"copies_example"}
    assert taxonomy.notes[0].label == "vague"
    assert len({n.span_id for n in taxonomy.notes}) == 10

    report = ws.label_all("song_ideas", budget=None, io=ScriptedIO(), yes=True)
    assert report.agreement == pytest.approx(0.9)  # the LLM disagrees with the one hand label
    assert report.labeled == report.outputs == 200 and not report.refused
    assert report.counts["copies_example"] == pytest.approx(80, abs=12)
    assert sum(report.counts.values()) == 200
    assert report.llm_calls == 10 + 190  # agreement check on the sample, then every other output
    rows = ws.db.query("SELECT source, count(*) AS n FROM labels GROUP BY source ORDER BY source")
    assert rows == [{"source": "human", "n": 10}, {"source": "llm", "n": 190}]


def test_ac7_low_agreement_is_refused_unless_forced(ws) -> None:
    labels = [""] * 10 + ["", ""] + ["-"] * 10  # keep notes and modes, but label every output "none"
    taxonomy = ws.review("song_ideas", io=ScriptedIO(labels), sample=10)
    assert all(n.label is None for n in taxonomy.notes)
    refused = ws.label_all("song_ideas", taxonomy, budget=None, yes=True)
    assert refused.agreement is not None and refused.agreement < 0.7
    assert refused.refused.startswith("the LLM agrees with the reviewed labels on only")
    assert refused.labeled == 0
    forced = ws.label_all("song_ideas", taxonomy, budget=None, yes=True, force=True)
    assert forced.labeled == 200 and not forced.refused
