"""Stage 1: statistics detectors, and how to follow a finding to its evidence.

What: the built-in detectors that need no model (`failure_rate`, `truncation`, `cost_latency`,
`gpu_thrash`, `reuse_health`, `selection_health`, `human_override`, `setup_smells`, `regression`) on runs
with planted problems, and what a `Finding` carries: scope, metric, affected / total, evidence span ids.

How: `ws.analyze(workflow, stages=("stats",))` runs every registered detector over the analytics tables of
that workflow and returns a `Report` with its findings, most important first. Each finding's `evidence`
holds up to 20 span ids; `ws.db.spans(ids)` returns those spans and `ws.db.query(sql)` the analytics rows,
so every claim can be checked by hand. `analyze(..., since=)` limits the analysis to recent runs.

Why: most production problems are countable (cut-off answers, failing judges, a slower prompt version)
and cheap to find without any LLM. Starting from numbers with evidence keeps the expensive stages focused.

Run: python examples/stats_detectors.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import synthetic_runs

PLANTS = [
    "truncation",  # 5% of generator calls stop at the token limit
    "score_zero_spike",  # failed judge answers recorded as a score of 0
    "judge_equals_generator",  # the judge is from the generator's model family
    "latency_regression_after_v4",  # prompt v4 made the generator slower
    "source_changed_version_unchanged",  # resumed runs continued a step whose code changed silently
]

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=600, plant=PLANTS)
    ws = tl.Workspace(Path(tmp) / "lens")
    ws.ingest(f"hone:{runs.root}")

    report = ws.analyze("song_ideas", stages=("stats",))
    for f in report.findings:
        print(f"{f.id} [{f.severity}] {f.detector}/{f.metric['name']}: {f.title}")
    found = {f.detector for f in report.findings}
    assert {runs.truth[p]["detector"] for p in PLANTS} <= found  # every plant found by its detector

    # A finding: where (scope), how much (affected / total, metric) and the evidence.
    cut = next(f for f in report.findings if f.metric["name"] == "truncation_rate")
    print("\nscope:", cut.scope)
    print("metric:", cut.metric, f"({cut.affected}/{cut.total} calls)")
    print("details:", cut.details)

    # Evidence is span ids: look at the recorded calls themselves ...
    first = ws.db.spans(cut.evidence[:1])[0]
    print("evidence span:", first["name"], first["attributes"]["gen_ai.response.finish_reasons"])
    # ... or at their analytics rows.
    sql = "SELECT finish_reason, input_tokens, context_limit FROM calls WHERE span_id = ?"
    rows = [ws.db.query(sql, (span_id,))[0] for span_id in cut.evidence]
    print("evidence row:", rows[0])
    assert {r["finish_reason"] for r in rows} == {"length"}

    # A regression finding records where the change happened.
    slower = next(f for f in report.findings if f.detector == "regression")
    print("\nregression boundary:", slower.details["boundary"])
    assert slower.details["boundary"]["to"] == "4"

    # `since` limits an analysis to runs that start at or after a time (ISO-8601, a datetime, or a duration
    # back from now such as "30d"): here the last quarter of the runs, all on prompt v4.
    recent = ws.analyze("song_ideas", since="2026-08-01T03:45:00Z", stages=("stats",))
    print("\nsince 03:45:", [(f.id, f.detector) for f in recent.findings])
    assert "regression" not in {f.detector for f in recent.findings}  # no version change inside the window

    # Findings are stored: the same issue found again keeps its id (matched by a content key).
    again = ws.analyze("song_ideas", stages=("stats",))
    assert [f.id for f in again.findings] == [f.id for f in report.findings]
