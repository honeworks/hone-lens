"""`hone_lens.adapters.flow` and `FakeFlowRuns` details the acceptance tests don't reach."""

import json
import sys
from dataclasses import dataclass, field

import pytest

from hone_lens.adapters.flow import FlowRuns, flow_source, step_row
from hone_lens.errors import SourceError
from hone_lens.testing import FakeFlowRuns


@pytest.mark.parametrize(
    ("target", "storage", "name"),
    [
        (
            "s3://hone-flow/projects/oneshotstudio/song_video",
            "s3://hone-flow/projects/oneshotstudio",
            "song_video",
        ),
        ("flows/song_video/", "flows", "song_video"),
        ("/abs/flows/w", "/abs/flows", "w"),
    ],
)
def test_flow_source_string_splits_storage_and_workflow(monkeypatch, target, storage, name) -> None:
    opened = []
    monkeypatch.setattr(
        "hone_lens.adapters.flow._open_runs", lambda s, n: opened.append((s, n)) or FakeFlowRuns()
    )
    source = flow_source(target)
    assert (source.storage, source.workflow, opened) == (storage, name, [(storage, name)])
    assert source.name == f"flow:{storage}/{name}"


@pytest.mark.parametrize("target", ["song_video", "s3://song_video", "/w", ""])
def test_flow_source_string_needs_storage_and_workflow(target) -> None:
    with pytest.raises(SourceError, match="needs a storage and a workflow name"):
        flow_source(target)


@dataclass(frozen=True)
class Summary:  # the shape of hone-flow's RunSummary (attributes, not a mapping)
    run_id: str = "r1"
    workflow: str = "w"
    created_at: str = "2026-09-01T10:00:00Z"
    updated_at: str = "2026-09-01T10:05:00Z"
    fork_of: str | None = "r0"


@dataclass(frozen=True)
class Record:  # the fields of hone-flow's StepRecord that hone-lens reads
    step: str = "lyrics"
    item: str | None = None
    kind: str = "global"
    status: str = "pending"
    attempt: int = 0
    attempts: list = field(default_factory=list)
    reused_from: str | None = None
    reviews: list = field(default_factory=list)
    review_note: str | None = None
    error: dict | None = None
    started_at: str | None = None
    duration_ms: int | None = None


def test_step_row_from_dataclasses_and_defaults() -> None:
    row = step_row(Summary(), Record())
    assert row["start_time"] == "2026-09-01T10:00:00.000Z"  # a pending step: the run's creation time
    assert (row["attempts"], row["attempt_statuses"], row["review_decisions"]) == (0, "[]", "[]")
    assert (row["fork_of"], row["error"], row["review_note_present"], row["item"]) == ("r0", None, 0, None)
    done = step_row(
        Summary(), Record(started_at="2026-09-01T10:01:00+02:00", error={"message": "x", "traceback": "t"})
    )
    assert done["start_time"] == "2026-09-01T08:01:00.000Z" and done["error"] == "x"


def test_step_records_carry_the_run_seed_from_the_manifest() -> None:
    fake = FakeFlowRuns()
    fake.add_run("seeded", [{"step": "s"}], seed=0, updated_at="2026-09-01T00:00:00Z")
    fake.add_run("unknown", [{"step": "s"}], updated_at="2026-09-01T00:00:00Z")  # no seed in the manifest
    assert fake.open_run("seeded").manifest["seed"] == 0 and "seed" not in fake.open_run("unknown").manifest
    source = FlowRuns("flows", "w", history=fake)
    source.read_new({})
    assert {r["run_id"]: r["run_seed"] for r in source.step_records(since=None)} == {
        "seeded": 0,
        "unknown": None,
    }
    assert step_row(Summary(), Record())["run_seed"] is None


def test_review_decisions_keep_the_actor_kind() -> None:
    reviews = [
        {
            "decision": "rejected",
            "actor": "render-check",
            "actor_kind": "automated",
            "note": "x",
            "at": "t",
            "attempt": 1,
        },
        {"decision": "approved", "actor": "ana", "at": "t", "attempt": 2},
    ]
    row = step_row(Summary(), Record(kind="gate", reviews=reviews))
    assert [d["actor_kind"] for d in json.loads(row["review_decisions"])] == ["automated", None]
    spans = FakeFlowRuns().add_run("r", [{"step": "check", "kind": "gate", "reviews": reviews}]).spans()
    assert [s["attributes"].get("hone.flow.gate.actor_kind") for s in spans[2:]] == ["automated", None]


def test_read_new_keeps_the_mark_when_nothing_changed() -> None:
    fake = FakeFlowRuns()
    source = FlowRuns("flows", "song_ideas", history=fake)
    assert source.read_new({}) == ([], {})
    fake.add_run("a", [{"step": "s"}], updated_at="2026-09-01T00:00:00+00:00")
    fake.add_run("b", [{"step": "s"}], updated_at="2026-09-01T01:00:00+02:00")  # earlier, in another zone
    spans, cursor = source.read_new({})
    assert cursor == {"since": "2026-09-01T00:00:00+00:00"} and len(spans) == 4
    later = {"since": "2026-09-03T00:00:00Z"}
    assert source.read_new(later) == ([], later)
    assert fake.asked[-1] == "2026-09-03T00:00:00Z"


def test_spans_since_filters_by_start_time() -> None:
    fake = FakeFlowRuns()
    fake.add_run(
        "a",
        [
            {"step": "s", "started_at": "2026-09-01T00:00:00Z"},
            {"step": "t", "started_at": "2026-09-05T00:00:00Z"},
        ],
        updated_at="2026-09-05T00:00:01Z",
    )
    source = FlowRuns("flows", "song_ideas", history=fake)
    assert [s["attributes"]["hone.step"] for s in source.spans(since="2026-09-02T00:00:00Z")] == ["t"]


def test_fake_flow_runs_fork_manifest_and_unknown_runs() -> None:
    fake = FakeFlowRuns()
    run = fake.add_run(
        "r1", [{"step": "g", "kind": "global"}, {"step": "s", "item": "a"}, {"step": "s", "item": "b"}]
    )
    fork = run.fork(refresh=("s",), items=["a"])
    assert [(r["step"], r["item"], r["reused_from"]) for r in fork.steps()] == [
        ("g", None, "r1"),
        ("s", "a", None),
    ]
    assert fork.manifest["fork_of"] == {"run_id": "r1"} and run.manifest["fork_of"] is None
    assert [r.run_id for r in fake.runs()] == ["r1-fork-1", "r1"]  # newest first
    with pytest.raises(KeyError, match="no run 'nope'"):
        fake.open_run("nope")
    failed = fake.add_run("r2", [{"step": "s", "status": "failed"}]).spans()
    assert failed[1]["status"]["code"] == "error"


def test_fake_fork_reruns_the_refreshed_step_and_everything_after_it() -> None:
    fake = FakeFlowRuns()
    run = fake.add_run("r1", [{"step": s, "item": "a"} for s in ("ideas", "lyrics", "render")])
    fork = run.fork(refresh=("lyrics",))
    assert {r["step"]: r["reused_from"] for r in fork.steps()} == {
        "ideas": "r1",
        "lyrics": None,
        "render": None,
    }
    assert {r["step"]: r["reused_from"] for r in run.fork().steps()} == dict.fromkeys(
        ("ideas", "lyrics", "render"), "r1"
    )


def test_fake_steps_that_never_ran_have_attempt_zero() -> None:
    run = FakeFlowRuns().add_run(
        "r1", [{"step": "s", "status": st} for st in ("pending", "skipped", "blocked", "failed")]
    )
    assert [r["attempt"] for r in run.steps()] == [0, 0, 0, 1]


def test_step_records_after_read_new_come_from_the_same_listing_else_are_read_again() -> None:
    fake = FakeFlowRuns()
    fake.add_run("a", [{"step": "s"}], updated_at="2026-09-01T00:00:00+00:00")
    source = FlowRuns("flows", "w", history=fake)
    source.read_new({})
    fake.add_run("b", [{"step": "s"}], updated_at="2026-09-02T00:00:00+00:00")  # added after the listing
    assert [r["run_id"] for r in source.step_records(since=None)] == ["a"]  # the listing read_new used
    asked = len(fake.asked)
    assert [r["run_id"] for r in source.step_records(since=None)] == ["b", "a"]  # handed out once: read now
    later = source.step_records(since="2026-09-01T12:00:00Z")
    assert [r["run_id"] for r in later] == ["b"] and fake.asked[asked:] == [None, "2026-09-01T12:00:00Z"]


def test_flow_source_without_hone_flow_names_the_extra(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "hone_flow", None)  # import hone_flow raises ImportError
    with pytest.raises(SourceError, match=r"pip install 'hone-lens\[flow\]'"):
        flow_source("flows/w")
