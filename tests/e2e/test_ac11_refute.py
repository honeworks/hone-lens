"""AC-11: a replay test that shows the suspected cause does not matter leaves the finding unconfirmed."""

import json

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, synthetic_runs

pytestmark = pytest.mark.e2e


def test_ac11_replay_refutes_a_wrong_cause(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=300, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), replayer=FakeReplayer())
    ws.ingest(f"hone:{runs.root}")
    (f,) = ws.analyze("song_ideas").findings
    ws.explain(f.id)
    t = ws.test(
        f.id, variants=2, samples=20
    )  # the default fake replayer: changing the section changes nothing
    assert not t.confirmed and t.metric_after == t.metric_before
    stored = ws.finding(f.id)
    assert stored.cause is not None and stored.cause.status == "refuted"
    assert stored.status == "new"  # the finding itself is never confirmed by the tool


def test_ac11_inconclusive_replay_stays_suspected(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=300, plant=["homogeneity_from_example"])
    other = "A quiet verse about a baker who plants tomatoes in a rainy suburb."

    def rarely_helps(span, overrides):  # changes about one output in eight: not beyond noise
        original = json.loads(span["attributes"]["gen_ai.output.messages"])[0]["content"]
        return other if span["span_id"].endswith(("0", "1")) else original

    ws = tl.Workspace(
        tmp_path / "lens", embedder=FakeEmbedder.semantic(), replayer=FakeReplayer(rarely_helps)
    )
    ws.ingest(f"hone:{runs.root}")
    (f,) = ws.analyze("song_ideas").findings
    t = ws.test(f.id, variants=1, samples=20)
    assert t.metric_before is not None and t.metric_after is not None
    assert not t.confirmed and t.metric_after < t.metric_before
    cause = ws.finding(f.id).cause
    assert cause is not None and cause.status == "suspected"
