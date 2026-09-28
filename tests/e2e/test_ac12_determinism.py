"""AC-12: the same data and seeds give the same clusters, findings and ids."""

import pytest

import hone_lens as tl
from hone_lens.testing import PLANTS, FakeEmbedder, synthetic_runs

pytestmark = pytest.mark.e2e


def _result(ws: tl.Workspace) -> tuple[list, list]:
    report = ws.analyze("song_ideas", stages=("stats", "outputs"))
    findings = [
        (f.id, f.key, f.title, f.severity, f.scope, f.affected, f.total, f.evidence, f.metric, f.details)
        for f in report.findings
    ]
    return findings, ws.db.query("SELECT id, size, center, members FROM clusters ORDER BY id")


def _analyze(root, *stores) -> tuple[list, list]:
    ws = tl.Workspace(root, embedder=FakeEmbedder.semantic())
    for store in stores:
        ws.ingest(f"hone:{store}")
    return _result(ws)


def test_ac12_same_data_same_clusters_findings_and_ids(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "a", n_runs=600, plant=list(PLANTS))
    again = synthetic_runs(tmp_path / "b", n_runs=600, plant=list(PLANTS))  # regenerated from the same seed
    first = _analyze(tmp_path / "lens1", runs.root)
    assert _analyze(tmp_path / "lens2", again.root) == first
    findings, clusters = first
    assert len(findings) >= 9 and clusters
    assert [f[0] for f in findings] == [f"F-{i:04d}" for i in range(1, len(findings) + 1)]


def test_ac12_ingest_order_does_not_matter(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "a", n_runs=300, plant=["homogeneity_from_example", "truncation"])
    one_go = _analyze(tmp_path / "lens1", runs.root)
    stores = [runs.root / "select", runs.root / "models", runs.flows]  # another order, three ingests
    assert _analyze(tmp_path / "lens2", *stores) == one_go
