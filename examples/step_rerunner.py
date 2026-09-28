"""Rerun workflow steps as a hone-flow fork: `FlowStepRerunner`, the `StepRerunner` port.

What: the port hone-lens uses to rerun whole workflow steps (not single model calls) for chosen items with
changed parameters, and its hone-flow implementation: a **fork** of the run, a new run beside the source
that reruns the step and everything downstream for those items and copies the rest.

How: `FlowStepRerunner(workflow).rerun(run_id, step, items, params)` calls
`workflow.open_run(run_id).fork(refresh=(step,), items=items, params=params)` and returns
`[new_run_id]`. Pass it as `Workspace(step_rerunner=...)`. Any object with that `rerun` method works;
`hone_lens.testing.check_step_rerunner` checks the contract. Here `FakeFlowRuns` stands in for the
hone-flow `Workflow` (it has the same `open_run(...).fork(...)`), so no hone-flow is needed. Ingesting the
fork shows what it reused (`reused_from`) and which run it forked (`fork_of`).

Why: some fixes cannot be tested one model call at a time (a changed step changes what later steps get).
A fork reruns exactly the affected part and never touches the source run, so the two can be compared.
`ws.test` does not use it yet (multi-step replay tests come in v2); the port and adapter are ready.

Run: python examples/step_rerunner.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns, FlowStepRerunner
from hone_lens.testing import FakeFlowRuns, check_step_rerunner

with tempfile.TemporaryDirectory() as tmp:
    workflow = FakeFlowRuns()  # in real use: the hone-flow Workflow, e.g. fk.Workflow("songs", storage=...)
    steps = ("ideas", "lyrics", "render")
    workflow.add_run("run-1", [{"step": s, "item": i} for s in steps for i in ("01", "02")], workflow="songs")

    rerunner = FlowStepRerunner(workflow)
    # The port's contract, checked on a throwaway workflow: the check makes a fork of its own.
    throwaway = FakeFlowRuns()
    throwaway.add_run("run-1", [{"step": "lyrics", "item": "02"}])
    check_step_rerunner(FlowStepRerunner(throwaway), "run-1", "lyrics", ["02"], {"style": "noir"})

    (new_run,) = rerunner.rerun("run-1", "lyrics", ["02"], {"style": "noir"})
    print("new run:", new_run)
    print("fork call:", workflow.forks[-1])
    assert workflow.forks[-1]["refresh"] == ("lyrics",)

    # The workspace keeps it for multi-step replay; reading the runs shows the fork's provenance.
    ws = tl.Workspace(Path(tmp) / "lens", step_rerunner=rerunner)
    # `history=` is what FlowRuns reads here; without it, it opens hone-flow runs at storage "flows"
    ws.ingest(FlowRuns("flows", "songs", history=workflow))
    rows = ws.db.query(
        "SELECT step, item, reused_from, fork_of FROM flow_steps WHERE run_id = ? ORDER BY step", (new_run,)
    )
    for r in rows:
        print(r)
    # lyrics and render (downstream) ran again for item 02; ideas was copied from run-1
    assert {r["step"]: r["reused_from"] for r in rows} == {"ideas": "run-1", "lyrics": None, "render": None}
    assert {r["fork_of"] for r in rows} == {"run-1"}
