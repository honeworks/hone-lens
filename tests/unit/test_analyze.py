from datetime import UTC, datetime

import pytest

import hone_lens as tl
from hone_lens.detectors import DETECTORS
from hone_lens.errors import HoneLensError
from hone_lens.mapping import since_time
from hone_lens.testing import FakeEmbedder, FakeRecordSource, FakeTextClient, synthetic_runs


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return synthetic_runs(tmp_path_factory.mktemp("syn"), n_runs=200, plant=["truncation", "vision_400"])


@pytest.fixture
def ws(tmp_path, runs) -> tl.Workspace:
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(f"hone:{runs.root}")
    return ws


@pytest.fixture
def custom():
    names = set(DETECTORS)
    yield
    for name in set(DETECTORS) - names:
        del DETECTORS[name]


def test_ids_are_stable_and_ranked(ws) -> None:
    first = ws.analyze(stages=("stats",))
    assert [f.id for f in first.findings] == [f"F-{i:04d}" for i in range(1, len(first.findings) + 1)]
    impacts = [f.impact for f in first.findings]
    assert impacts == sorted(impacts, reverse=True)
    again = ws.analyze(stages=("stats",))
    assert [(f.id, f.key) for f in again.findings] == [(f.id, f.key) for f in first.findings]
    assert ws.findings() == sorted(again.findings, key=lambda f: f.id)


def test_dismissed_findings_stay_dismissed(ws) -> None:
    first = ws.analyze(stages=("stats",))
    dismissed = ws.set_status(first.findings[0].id, "dismissed", note="known; won't fix")
    assert dismissed.details["note"] == "known; won't fix"
    again = ws.analyze(stages=("stats",))
    assert dismissed.id not in [f.id for f in again.findings]
    assert [f.id for f in ws.findings(status="dismissed")] == [dismissed.id]
    assert dismissed.id not in ws.report()


def test_status_and_id_errors(ws) -> None:
    ws.analyze(stages=("stats",))
    with pytest.raises(HoneLensError, match="unknown status 'nope'"):
        ws.set_status("F-0001", "nope")
    with pytest.raises(HoneLensError, match="no finding 'F-9999'"):
        ws.finding("F-9999")
    with pytest.raises(HoneLensError, match="unknown stage"):
        ws.analyze(stages=("stats", "magic"))


def test_workflow_and_since_scope_the_analysis(ws) -> None:
    assert ws.analyze("other_workflow", stages=("stats",)).findings == []
    assert ws.analyze("song_ideas", since="2030-01-01", stages=("stats",)).findings == []
    assert ws.analyze("song_ideas", since="2026-08-01T01:00:00Z", stages=("stats",)).findings
    assert ws.db.query("SELECT count(*) AS n FROM calls")[0]["n"] == 600  # the full tables are back


def test_no_stages_no_findings(ws) -> None:
    assert ws.analyze(stages=()).findings == []


def test_custom_detector_runs_and_a_failing_one_is_reported(ws, custom) -> None:
    @tl.detector(category="quality")
    def long_outputs(db: tl.AnalyticsDB) -> list[tl.Finding]:
        rows = db.query("SELECT span_id FROM outputs WHERE length(text) > 10")
        return [
            tl.Finding("outputs are long", affected=len(rows), total=len(rows), evidence=[rows[0]["span_id"]])
        ]

    @tl.detector(category="cost", name="broken")
    def _broken(db: tl.AnalyticsDB) -> list[tl.Finding]:
        raise RuntimeError("bad SQL")

    with pytest.raises(HoneLensError, match="a detector named 'truncation' is already registered"):
        tl.detector(name="truncation")(long_outputs)
    report = ws.analyze(stages=("stats",))
    (mine,) = [f for f in report.findings if f.detector == "long_outputs"]
    assert mine.category == "quality" and mine.id.startswith("F-")
    assert report.notes == ["detector broken failed: RuntimeError: bad SQL"]


def test_report_terminal_text_and_file(ws, tmp_path) -> None:
    report = ws.analyze("song_ideas", stages=("stats",))
    f = report.findings[0]
    f.cause = tl.Cause("model", "gemma4-12b", hypothesis="too small")
    f.fix = tl.Fix("model_swap", "use a vision model")
    f.test = tl.TestResult(True, "error_rate", 1.0, 0.0)
    ws.db.save_findings([f])
    text = ws.report("song_ideas", path=tmp_path / "r.txt")
    assert (tmp_path / "r.txt").read_text() == text
    assert text.startswith("hone-lens: song_ideas\n")
    assert f"{f.id}  [{f.severity}]" in text and "cause (suspected): model gemma4-12b" in text
    assert "fix: use a vision model" in text and "test (confirmed): error_rate 1.0 -> 0.0" in text
    assert ws.report("other").count("F-") == 0


def test_since_time() -> None:
    assert since_time(None) is None
    assert since_time("2026-09-01") == "2026-09-01T00:00:00.000Z"
    assert since_time(datetime(2026, 9, 1, 12)) == "2026-09-01T12:00:00.000Z"  # noqa: DTZ001 - naive means UTC
    ago = datetime.fromisoformat(str(since_time("30d")).replace("Z", "+00:00"))
    assert 29.9 < (datetime.now(UTC) - ago).total_seconds() / 86400 < 30.1
    assert str(since_time("1d")) < str(since_time("12h")) < str(since_time("45m"))
    with pytest.raises(HoneLensError, match="duration like '30d'"):
        since_time("last week")


def test_describe_notes_failures_and_skips(tmp_path) -> None:
    runs = synthetic_runs(tmp_path / "syn", n_runs=100, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient(["no json"]))
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze()
    (cluster_id,) = [r["id"] for r in ws.db.query("SELECT id FROM clusters")]
    assert f"cluster {cluster_id} not described: response is not valid JSON for the schema" in report.notes
    assert report.llm_calls == 1 and not report.budget_exhausted
    no_llm = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic()).analyze()
    assert "describe stage skipped: no analysis LLM; pass Workspace(llm=...)" in no_llm.notes


def _two_workflows(tmp_path) -> tl.Workspace:
    a = synthetic_runs(tmp_path / "a", n_runs=60, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
    ws.ingest(f"hone:{a.root}")
    spans = [
        {**s, "resource": {"service.name": "other"}} for s in tl.sources.OtlpJsonFiles(a.otlp_path).spans()
    ]
    ws.ingest(FakeRecordSource(spans))  # the same outputs as a plain OTel workflow "other"
    return ws


def test_budget_limits_through_analyze(tmp_path) -> None:
    ws = _two_workflows(tmp_path)
    stopped = ws.analyze("song_ideas", budget="0 calls")
    assert stopped.llm_calls == 0 and stopped.budget_exhausted
    assert "describe stopped: call budget of 0 spent; 1 clusters left undescribed" in stopped.notes
    tokens = ws.analyze("song_ideas", budget="10 tokens")
    assert tokens.llm_calls == 0 and tokens.budget_exhausted
    with pytest.raises(HoneLensError, match="budget 'plenty' not understood"):
        ws.analyze(budget="plenty")


def test_describe_only_the_analyzed_workflow_and_report_per_call_spend(tmp_path) -> None:
    ws = _two_workflows(tmp_path)
    wallet = tl.Budget(calls=10)
    first = ws.analyze("song_ideas", budget=wallet)
    assert first.llm_calls == 1
    described = ws.db.query("SELECT workflow FROM clusters WHERE description IS NOT NULL")
    assert described == [{"workflow": "song_ideas"}]
    second = ws.analyze("other", budget=wallet)
    assert second.llm_calls == 1 and wallet.spent_calls == 2  # one wallet, per-analysis report
