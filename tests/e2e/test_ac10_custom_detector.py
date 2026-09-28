"""AC-10: a custom detector is registered and run; its findings appear in the reports."""

import json

import pytest

import hone_lens as tl
from hone_lens.detectors import DETECTORS
from hone_lens.testing import synthetic_runs

pytestmark = pytest.mark.e2e


@pytest.fixture
def registry():
    before = dict(DETECTORS)
    yield
    DETECTORS.clear()
    DETECTORS.update(before)


def test_ac10_custom_detector_in_every_report(tmp_path, registry) -> None:
    @tl.detector(category="quality")
    def ideas_too_long(db: tl.AnalyticsDB) -> list[tl.Finding]:
        rows = db.query("SELECT span_id, start_time FROM outputs WHERE length(text) > 150")
        total = db.query("SELECT count(*) AS n FROM outputs")[0]["n"]
        if not rows:
            return []
        return [
            tl.Finding(
                f"{len(rows)} song ideas are longer than 150 characters",
                severity="low",
                scope={"workflow": "song_ideas"},
                affected=len(rows),
                total=total,
                metric={"name": "long_idea_rate", "value": len(rows) / total},
                evidence=[r["span_id"] for r in rows[:5]],
            )
        ]

    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats",))
    (mine,) = [f for f in report.findings if f.detector == "ideas_too_long"]
    assert mine.category == "quality" and mine.id.startswith("F-")
    assert mine.title in ws.report("song_ideas")
    assert mine.id in [f["id"] for f in json.loads(ws.report("song_ideas", fmt="json"))["findings"]]
    html = ws.report("song_ideas", fmt="html")
    assert f'<section id="{mine.id}">' in html and 'href="#trace-' in html
