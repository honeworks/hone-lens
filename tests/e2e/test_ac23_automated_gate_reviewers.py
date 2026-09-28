"""AC-23: gate decisions recorded with `actor_kind = "automated"` (a script behind the gate API, such as a
render check) are not reported as decisions by people; the automated gate gets "rounds until approved"
instead, and its rejections are not counted as review revisions
([0005](../../design/changes/0005-automated-gate-reviewers.md))."""

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.testing import FakeFlowRuns

pytestmark = pytest.mark.e2e


def _runs(actor_kind: str | None) -> FakeFlowRuns:
    """10 runs; a reviewer sends `code` back once in each and then approves."""
    fake = FakeFlowRuns()
    kind = {"actor_kind": actor_kind} if actor_kind else {}
    reviews = [
        {"decision": d, "actor": "render-check", "at": f"2026-09-01T00:0{n}:00Z", "attempt": n + 1, **kind}
        for n, d in enumerate(["rejected", "approved"])
    ]
    for n in range(10):
        fake.add_run(
            f"r{n}",
            [
                {
                    "step": "code",
                    "item": "01",
                    "attempt": 2,
                    "attempts": [{"attempt": 1, "status": "rejected"}],
                },
                {"step": "render_check", "item": "01", "kind": "gate", "reviews": reviews, "attempt": 2},
            ],
            workflow="concept_shorts",
        )
    return fake


def _findings(tmp_path, actor_kind: str | None) -> dict[str, tl.Finding]:
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FlowRuns("flows", "concept_shorts", history=_runs(actor_kind)))
    return {f.metric["name"]: f for f in ws.analyze("concept_shorts", stages=("stats",)).findings}


def test_ac23_automated_reviewers_get_rounds_until_approved(tmp_path) -> None:
    found = _findings(tmp_path, "automated")
    assert set(found) == {"automated_rework_rate"}
    f = found["automated_rework_rate"]
    assert (f.scope["gate"], f.affected, f.total, f.details["mean_rounds"]) == ("render_check", 10, 10, 1.0)
    assert f.severity != "high" and "people" not in f.title


def test_ac23_people_and_older_runs_are_decisions_by_people(tmp_path) -> None:
    for n, kind in enumerate(("person", None)):  # no actor kind: a person, as before 0005
        found = _findings(tmp_path / str(n), kind)
        assert set(found) == {"override_rate", "revision_rate"}
        assert "by people" in found["override_rate"].title
