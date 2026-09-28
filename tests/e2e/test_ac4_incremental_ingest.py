"""AC-4: a second ingest after adding 100 runs reads only the new spans, and the results update."""

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, synthetic_runs

pytestmark = pytest.mark.e2e

SPANS_PER_RUN = 7  # flow run + step, generator + judge call, select run + score + decision


def _spans(n_runs: int) -> int:
    return n_runs * SPANS_PER_RUN + n_runs // 5  # every fifth run was resumed: one more flow run span


def _outputs(ws: tl.Workspace) -> int:
    return ws.db.query("SELECT count(*) AS n FROM outputs WHERE workflow = 'song_ideas'")[0]["n"]


def test_ac4_incremental_ingest_hone_stores(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=300, seed=0)
    ws = tl.Workspace(tmp_path / "lens")
    (first,) = ws.ingest(f"hone:{runs.root}")
    assert first.read == first.added == _spans(300)
    assert _outputs(ws) == 300

    (again,) = ws.ingest(f"hone:{runs.root}")
    assert again.read == 0  # nothing new: nothing read

    synthetic_runs(tmp_path, n_runs=100, seed=1)  # 100 more runs appended to the same stores
    (second,) = ws.ingest(f"hone:{runs.root}")
    assert second.read == second.added == _spans(100)
    assert _outputs(ws) == 400
    newest = ws.db.query("SELECT max(start_time) AS t FROM outputs")[0]["t"]
    assert newest.startswith("2026-08-21")  # seed 1 runs start 20 days later


def test_ac4_incremental_ingest_otlp_files(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=300, seed=0)
    ws = tl.Workspace(tmp_path / "lens")
    pattern = f"otlp:{runs.otlp_path.parent}/*.json"
    assert ws.ingest(pattern)[0].added == 600
    assert ws.ingest(pattern)[0].read == 0

    synthetic_runs(tmp_path, n_runs=100, seed=1)  # adds traces-1.json next to traces-0.json
    (second,) = ws.ingest(pattern)
    assert second.read == second.added == 200
    assert _outputs(ws) == 400


def test_ac4_reanalysis_embeds_only_new_outputs(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example"], seed=0)
    embedder = FakeEmbedder.semantic()
    ws = tl.Workspace(tmp_path / "lens", embedder=embedder)
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas", stages=("outputs",))
    first = sum(map(len, embedder.calls))
    assert first >= 150  # every distinct output (and the prompt sections) embedded once

    embedder.calls.clear()
    reopened = tl.Workspace(tmp_path / "lens", embedder=embedder)
    reopened.analyze("song_ideas", stages=("outputs",))
    assert embedder.calls == []  # everything came from the workspace cache

    synthetic_runs(tmp_path, n_runs=100, seed=1)
    reopened.ingest(f"hone:{runs.root}")
    reopened.analyze("song_ideas", stages=("outputs",))
    new_texts = reopened.db.query(
        "SELECT count(DISTINCT text_sha) AS n FROM outputs WHERE start_time >= '2026-08-21'"
    )
    assert sum(map(len, embedder.calls)) == new_texts[0]["n"]
