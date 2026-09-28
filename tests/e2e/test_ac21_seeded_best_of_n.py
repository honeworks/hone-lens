"""AC-21: best-of-N inside a seeded hone-flow run (the same prompt sent with the variation seeds
`ctx.seed`, `ctx.seed + 1`, ...) is not reported as unseeded; the same calls in runs that record no seed
still are ([0003](../../design/changes/0003-seeded-best-of-n-is-not-unseeded.md))."""

import json

import pytest

import hone_lens as tl
from hone_lens.adapters.flow import FlowRuns
from hone_lens.testing import FakeFlowRuns, FakeRecordSource
from hone_lens.testing.flow import FakeRun

pytestmark = pytest.mark.e2e


def _best_of_three(run: FakeRun) -> list[dict]:
    """Three `hone.models.chat` calls under the run's `lyrics` step: one prompt, seeds 100, 101, 102."""
    step = next(s for s in run.spans() if s["attributes"].get("hone.step") == "lyrics")
    prompt = json.dumps([{"role": "user", "content": "Write lyrics about the sea."}])
    return [
        {
            **step,
            "span_id": f"{step['span_id'][:14]}{n:02x}",
            "parent_span_id": step["span_id"],
            "name": "hone.models.chat",
            "kind": "client",
            "attributes": {
                "hone.run_id": run.run_id,
                "hone.step": "lyrics",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gemma4-12b",
                "gen_ai.request.seed": 100 + n,
                "gen_ai.input.messages": prompt,
            },
        }
        for n in range(3)
    ]


def _analyze(tmp_path, seed: int | None) -> list[tl.Finding]:
    fake = FakeFlowRuns()
    runs = [fake.add_run(f"r{n}", [{"step": "lyrics", "item": "01"}], seed=seed) for n in range(4)]
    ws = tl.Workspace(tmp_path / "lens")
    ws.ingest(FlowRuns("flows", "song_ideas", history=fake))
    ws.ingest(FakeRecordSource([span for run in runs for span in _best_of_three(run)], name="models"))
    return ws.analyze("song_ideas", stages=("stats",)).findings


def test_ac21_seeded_best_of_n_is_not_unseeded(tmp_path) -> None:
    assert _analyze(tmp_path, seed=0) == []


def test_ac21_runs_without_a_recorded_seed_still_are(tmp_path) -> None:
    (f,) = _analyze(tmp_path, seed=None)
    assert (f.metric["name"], f.scope["step"], f.affected, f.severity) == (
        "unseeded_repeat_rate",
        "lyrics",
        12,
        "low",
    )
