"""Read hone-flow runs: run folders through `hone:`, and step records through `FlowRuns` (`flow:`).

What: how hone-lens sees workflows run with hone-flow: step statuses (`done`, `failed`, `skipped`,
`blocked`, `awaiting_review`), attempts, review decisions, and which results a fork reused from another
run.

How: hone-flow keeps every run in its own folder (`<storage>/<workflow>/runs/<run_id>/`), spans in
`spans.jsonl`. `ws.ingest("hone:<folder>")` reads every `spans.jsonl` below a local folder into the
`steps`, `selections` and `run_calls` tables. `FlowRuns(storage, name)` (source string
`"flow:<storage>/<workflow>"`, extra `hone-lens[flow]`) reads through hone-flow's read API instead, so runs
on S3 work too, and also fills `flow_steps` with each step's record (earlier attempts and their statuses,
review decisions). Here `FakeFlowRuns` stands in for `fk.open_runs(storage, name)`, so no hone-flow is
needed; with hone-flow installed, drop `history=` and the same code reads the real runs.

Why: a step that is skipped, waiting for a person or copied by a fork is neither a success nor a failure;
counting it as either would make failure rates and durations wrong. And reject-and-revise cycles at a
review gate are a quality signal of their own (`human_override`'s `revision_rate`).

Run: python examples/flow_run_folders.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.testing import FakeFlowRuns, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # --- 1. Run folders on local disk: `hone:<folder>` -------------------------------------------------
    runs = synthetic_runs(root, n_runs=50)  # run folders under runs.flows / "song_ideas" / "runs"
    ws = tl.Workspace(root / "lens")
    (ingested,) = ws.ingest(f"hone:{runs.flows / 'song_ideas' / 'runs'}")  # one workflow's runs/ folder
    run_folders = list(runs.flows.glob("*/runs/*/spans.jsonl"))
    print(f"hone: {ingested.added} spans from {len(run_folders)} run folders")
    step_statuses = ws.db.query("SELECT status, count(*) AS n FROM steps GROUP BY status")
    print(step_statuses)
    assert step_statuses == [{"status": "done", "n": 50}]
    # one row per run / resume / fork call: every fifth synthetic run was interrupted and resumed
    print(ws.db.query("SELECT status, count(*) AS n FROM run_calls GROUP BY status ORDER BY status"))

    # --- 2. The read API: `FlowRuns` fills `flow_steps` too ---------------------------------------------
    history = FakeFlowRuns()  # in real use: hone-flow's runs of workflow "songs" in s3://bucket/projects
    rejected_once = [{"attempt": 1, "status": "rejected"}]
    rejected_twice = [*rejected_once, {"attempt": 2, "status": "rejected"}]
    approved = [{"decision": "approved", "actor": "ana", "at": "2026-09-01T12:00:00Z", "attempt": 3}]
    history.add_run(
        "run-1",
        [
            # lyrics for song 01 were rejected twice by the reviewer before the third attempt was approved
            {
                "step": "lyrics",
                "item": "01",
                "attempt": 3,
                "attempts": rejected_twice,
                "review_note": "warmer",
            },
            {"step": "review", "item": "01", "kind": "gate", "reviews": approved},
            {"step": "render", "item": "01"},
            # songs 04 and 05: rejected once each, then approved
            {"step": "lyrics", "item": "04", "attempt": 2, "attempts": rejected_once},
            {"step": "lyrics", "item": "05", "attempt": 2, "attempts": rejected_once},
            # song 02 failed, so its downstream steps are blocked; 03 waits at the gate
            {"step": "lyrics", "item": "02", "status": "failed", "error": {"message": "ValueError: empty"}},
            {"step": "review", "item": "02", "kind": "gate", "status": "blocked"},
            {"step": "render", "item": "02", "status": "blocked"},
            {"step": "lyrics", "item": "03"},
            {"step": "review", "item": "03", "kind": "gate", "status": "awaiting_review"},
            {"step": "render", "item": "03", "status": "pending"},
        ],
        workflow="songs",
        status="awaiting_review",
    )
    # a fork of run-1 that reran `render` for song 01 and copied everything upstream (`reused_from`)
    history.open_run("run-1").fork(refresh=("render",), items=["01"])

    songs = tl.Workspace(root / "songs-lens")
    # the same as songs.ingest("flow:s3://bucket/projects/songs") with hone-flow installed
    songs.ingest(FlowRuns("s3://bucket/projects", "songs", history=history))
    records = songs.db.query(
        "SELECT run_id, step, item, status, attempt, attempt_statuses, reused_from, fork_of FROM flow_steps "
        "WHERE step = 'lyrics' ORDER BY run_id, item"
    )
    for r in records:
        print(r)
    assert records[0]["attempt_statuses"] == '["rejected", "rejected"]'
    assert records[-1]["reused_from"] == "run-1" and records[-1]["fork_of"] == "run-1"

    # Statuses are told apart in `steps`; only steps that ran count as work (`executed`).
    statuses = songs.db.query("SELECT status, sum(executed) AS ran, count(*) AS n FROM steps GROUP BY status")
    print(statuses)
    assert {s["status"] for s in statuses} >= {"done", "failed", "blocked", "awaiting_review", "pending"}

    # Stage 1 reads both tables. `human_override`: 3 of the 5 lyrics that ran in run-1 went through
    # reject-and-revise cycles (the fork's copy of song 01 is not work done, so it is not counted). The
    # failure rate (1 of 5) stays below the 3 failures a finding needs.
    report = songs.analyze("songs", stages=("stats",))
    (revisions,) = report.findings
    print(f"{revisions.id} {revisions.detector}: {revisions.title}")
    assert (revisions.metric["name"], revisions.affected, revisions.total) == ("revision_rate", 3, 5)
    assert revisions.details == {"revisions": 4, "max_revisions": 2}
