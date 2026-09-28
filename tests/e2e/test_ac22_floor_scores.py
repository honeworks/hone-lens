"""AC-22: a judge on a 1-5 scale (mapped to 0, 0.25, ..., 1) that often answers "1" with a reason gets a
`floor_score_rate` finding with sample reasons, not "failed scores recorded as 0"; zeros without the
judge's answer still get `zero_score_rate`
([0004](../../design/changes/0004-zero-scores-on-discrete-scales.md))."""

import pytest

import hone_lens as tl
from hone_lens.testing import FakeRecordSource

pytestmark = pytest.mark.e2e

REASON = "The image is a painting, which is not a clean, well-composed picture"


def _scores(zero: dict) -> list[dict]:
    """40 `hone.select.score` spans of scorer `clean`: 12 zeros with `zero` attributes, the rest above."""
    values = [0.0] * 12 + [0.25] * 2 + [0.5] * 8 + [0.75] * 12 + [1.0] * 6
    return [
        {
            "trace_id": f"{n:032x}",
            "span_id": f"{n:016x}",
            "parent_span_id": None,
            "name": "hone.select.score",
            "kind": "internal",
            "start_time": f"2026-09-28T10:00:{n:02d}Z",
            "end_time": f"2026-09-28T10:00:{n:02d}Z",
            "status": {"code": "ok", "message": ""},
            "attributes": {
                "hone.select.scorer": "clean",
                "hone.candidate_id": f"frame-{n}",
                "hone.select.score.value": value,
                **(
                    {"hone.select.score.error": ""}
                    | (zero if value == 0 else {"hone.select.score.confidence": 0.9})
                ),
            },
            "events": [],
            "resource": {"service.name": "oneshot"},
            "links": [],
        }
        for n, value in enumerate(values)
    ]


def _findings(tmp_path, zero: dict) -> list[tl.Finding]:
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FakeRecordSource(_scores(zero)))
    return ws.analyze("oneshot", stages=("stats",)).findings


def test_ac22_explained_zeros_are_floor_scores(tmp_path) -> None:
    (f,) = _findings(tmp_path, {"hone.select.score.confidence": 0.9, "hone.select.score.reason": REASON})
    assert (f.metric["name"], f.scope["scorer"], f.affected, f.total) == ("floor_score_rate", "clean", 12, 40)
    assert "lowest possible" in f.title and REASON in f.details["sample_reasons"]


def test_ac22_unexplained_zeros_are_possibly_failed(tmp_path) -> None:
    (f,) = _findings(tmp_path, {})
    assert (f.metric["name"], f.affected) == ("zero_score_rate", 12) and "failed scores" in f.title
