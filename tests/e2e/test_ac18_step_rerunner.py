"""AC-18: `StepRerunner` as a hone-flow fork (`FlowStepRerunner`), and `check_step_rerunner`."""

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowStepRerunner
from hone_lens.testing import FakeFlowRuns, FakeStepRerunner, check_step_rerunner

pytestmark = pytest.mark.e2e


def _workflow() -> FakeFlowRuns:
    fake = FakeFlowRuns()  # a fake hone-flow workflow: open_run(run_id).fork(...)
    fake.add_run("r1", [{"step": s, "item": i} for s in ("ideas", "lyrics", "render") for i in ("01", "02")])
    return fake


def test_ac18_rerun_is_a_fork_of_the_run() -> None:
    wf = _workflow()
    rerunner = FlowStepRerunner(wf)
    new_ids = rerunner.rerun("r1", "lyrics", ["02"], {"style": "noir"})
    assert new_ids == ["r1-fork-1"]
    assert wf.forks == [
        {"run_id": "r1", "refresh": ("lyrics",), "items": ["02"], "params": {"style": "noir"}}
    ]
    fork = wf.open_run("r1-fork-1")
    assert fork.fork_of == "r1" and {r["item"] for r in fork.records} == {"02"}


def test_ac18_contract_checker_passes_and_workspace_accepts_it(tmp_path) -> None:
    wf = _workflow()
    check_step_rerunner(FlowStepRerunner(wf), "r1", "lyrics", ["01"], {"style": "noir"})
    assert wf.forks[-1] == {
        "run_id": "r1",
        "refresh": ("lyrics",),
        "items": ["01"],
        "params": {"style": "noir"},
    }
    check_step_rerunner(FakeStepRerunner(), "r1", "lyrics", ["01"])
    ws = tl.Workspace(tmp_path, step_rerunner=FlowStepRerunner(wf))
    assert isinstance(ws.step_rerunner, FlowStepRerunner)


def test_ac18_contract_checker_catches_a_rerunner_that_changes_the_source_run() -> None:
    class InPlace:
        def rerun(self, run_id, step, items, params):
            return [run_id]

    with pytest.raises(AssertionError, match="new run"):
        check_step_rerunner(InPlace(), "r1", "lyrics", ["01"])

    class Nothing:
        def rerun(self, run_id, step, items, params):
            return []

    with pytest.raises(AssertionError, match="no run id"):
        check_step_rerunner(Nothing(), "r1", "lyrics", ["01"])
