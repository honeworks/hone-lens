import pytest

from hone_lens.findings import Finding, finding, pct, ranked, rate_finding, sample_ids, severity_for, where


def _rows(n: int) -> list[dict]:
    return [{"span_id": f"{i:016x}", "start_time": f"2026-09-01T00:00:{i:02d}.000Z"} for i in range(n)]


def test_finding_from_rows() -> None:
    scope = {"workflow": "w", "step": None}
    f = finding(
        "title",
        category="quality",
        scope=scope,
        affected=reversed(_rows(30)),
        total=100,
        severity="high",
        detector="d",
    )
    assert (f.affected, f.total, f.detector, f.severity) == (30, 100, "d", "high")
    assert f.scope == {
        "workflow": "w",
        "time_range": ["2026-09-01T00:00:00.000Z", "2026-09-01T00:00:29.000Z"],
    }
    assert len(f.evidence) == 20 and f.evidence[0] == f"{0:016x}"
    assert f.id == "" and f.status == "new"


def test_sample_ids_spread_and_short_lists() -> None:
    assert sample_ids(_rows(3)) == [f"{i:016x}" for i in range(3)]
    assert sample_ids(_rows(40), n=4) == [f"{i:016x}" for i in (0, 10, 20, 30)]


def test_key_ignores_time_range_and_title() -> None:
    a = Finding("one", "quality", scope={"workflow": "w", "time_range": [1, 2]}, metric={"name": "m"})
    b = Finding("two", "quality", scope={"workflow": "w", "time_range": [3, 4]}, metric={"name": "m"})
    c = Finding("one", "quality", scope={"workflow": "x"}, metric={"name": "m"})
    assert a.key == b.key != c.key


def test_impact_and_ranking() -> None:
    small = Finding("s", "quality", severity="high", affected=1, total=10)
    big = Finding("b", "quality", severity="low", affected=9, total=10)
    cost = Finding("c", "cost", severity="low", affected=9, total=10)
    empty = Finding("e", "quality", total=0)
    assert small.impact == pytest.approx(0.3) and big.impact == pytest.approx(0.9)
    assert cost.impact < big.impact and empty.impact == 0
    assert ranked([small, empty, big]) == [big, small, empty]


@pytest.mark.parametrize(("rate", "expected"), [(0.5, "high"), (0.1, "medium"), (0.01, "low")])
def test_severity_for(rate: float, expected: str) -> None:
    assert severity_for(rate) == expected


def test_labels() -> None:
    assert pct(0.4) == "40%" and pct(0.004) == "0.4%" and pct(0) == "0%"
    assert where({"workflow": "w", "step": "s", "model": "m"}) == "w/s (model m)"
    assert where({}) == "all runs"


def test_rate_finding_thresholds() -> None:
    hit = _rows(3)
    (f,) = rate_finding(
        "calls failed",
        category="reliability",
        scope={"workflow": "w"},
        hit=hit,
        total=10,
        metric="error_rate",
        min_rate=0.1,
    )
    assert f.title == "30% of w calls failed" and f.severity == "high"
    assert f.metric == {"name": "error_rate", "value": 0.3, "baseline": 0.0}
    assert rate_finding("x", category="c", scope={}, hit=hit, total=100, metric="m", min_rate=0.05) == []
    assert rate_finding("x", category="c", scope={}, hit=hit[:2], total=2, metric="m", min_rate=0.0) == []
    assert rate_finding(
        "x", category="c", scope={}, hit=[], total=0, metric="m", min_rate=0.0, min_affected=0
    )
