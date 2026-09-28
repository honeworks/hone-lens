"""AC-13: 10,000 synthetic runs analyzed (stages 1-2, fake embedder) in under 60 s."""

import time

import pytest

import hone_lens as tl
from hone_lens.testing import PLANTS, FakeEmbedder, synthetic_runs

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def test_ac13_ten_thousand_runs_in_under_a_minute(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=10_000, plant=list(PLANTS))
    start = time.perf_counter()
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic())
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats", "outputs"))
    elapsed = time.perf_counter() - start
    assert elapsed < 60, f"ingest + stages 1-2 took {elapsed:.1f} s"
    assert ws.db.query("SELECT count(*) AS n FROM outputs")[0]["n"] == 10_000
    detectors = {f.detector for f in report.findings}
    assert {"homogeneity", "truncation", "regression", "gpu_thrash", "setup_smells"} <= detectors
