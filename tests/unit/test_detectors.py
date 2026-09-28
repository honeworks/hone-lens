"""Detector branches the synthetic plants don't reach, on hand-made analytics rows."""

import itertools
import json
import sqlite3

import pytest

from hone_lens import AnalyticsDB
from hone_lens.derive import COLUMNS
from hone_lens.detectors import changes
from hone_lens.detectors.changes import regression
from hone_lens.detectors.reliability import failure_rate, reuse_health
from hone_lens.detectors.resources import cost_latency, gpu_thrash
from hone_lens.detectors.review import human_override
from hone_lens.detectors.selection import selection_health
from hone_lens.detectors.setup import family, setup_smells
from hone_lens.schema import FLOW_STEPS

_ids = itertools.count()


@pytest.fixture
def db(tmp_path) -> AnalyticsDB:
    return AnalyticsDB(tmp_path / "lens.db")


def put(db: AnalyticsDB, table: str, rows: list[dict]) -> None:
    with sqlite3.connect(db.path) as con:
        for row in rows:
            i = next(_ids)
            full = {
                "span_id": f"{i:016x}",
                "trace_id": f"{i:032x}",
                "run_id": f"run-{i}",
                "workflow": "w",
                "start_time": f"2026-09-01T{i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d}.000Z",
                **({"executed": 1} if table == "steps" else {}),
                **row,
            }
            assert set(full) <= set(COLUMNS[table])
            names = ", ".join(full)
            con.execute(
                f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(full))})",
                list(full.values()),
            )
    con.close()


def test_step_error_rate(db) -> None:
    put(db, "steps", [{"step": "s", "status": "error"}] * 5 + [{"step": "s", "status": "ok"}] * 5)
    (f,) = failure_rate(db)
    assert f.metric["name"] == "step_error_rate" and f.affected == 5 and f.severity == "high"


def test_step_error_rate_counts_hone_flow_failed_and_only_executed_steps(db) -> None:
    rows = [{"step": "s", "status": "failed"}] * 3 + [{"step": "s", "status": "done"}] * 7
    # paused, skipped, blocked and fork-copied steps are neither failures nor successes
    rows += [
        {"step": "s", "status": st, "executed": 0} for st in ("skipped", "blocked", "awaiting_review")
    ] * 10
    rows += [{"step": "s", "status": "done", "reused_from": "r0", "executed": 0}] * 10
    put(db, "steps", rows)
    (f,) = failure_rate(db)
    assert (f.metric["name"], f.affected, f.total) == ("step_error_rate", 3, 10)


def _run_calls(run: str, n_calls: int, warn_step: str | None = None) -> list[dict]:
    rows = [{"run_id": run, "status": "interrupted"}] * (n_calls - 1) + [
        {"run_id": run, "status": "completed"}
    ]
    if warn_step:
        warning = {"kind": "source_changed_version_unchanged", "step": warn_step, "old": "a", "new": "b"}
        rows[-1] = {**rows[-1], "warnings": json.dumps([warning, {"kind": "other", "step": "x"}])}
    return rows


def test_reuse_health_rate_is_per_resumed_run_and_step(db) -> None:
    rows = [r for i in range(18) for r in _run_calls(f"ok-{i}", 2)]  # resumed, no warning
    rows += [r for i in range(2) for r in _run_calls(f"warn-{i}", 3, "lyrics")]
    rows += [r for i in range(30) for r in _run_calls(f"once-{i}", 1)]  # never resumed: not counted
    put(db, "run_calls", rows)
    (f,) = reuse_health(db)
    assert (f.detector, f.metric["name"], f.affected, f.total) == ("", "stale_source_rate", 2, 20)
    assert f.scope["step"] == "lyrics" and f.scope["workflow"] == "w" and f.severity == "high"
    assert len(f.evidence) == 2  # one evidence span per affected run
    assert "other" not in json.dumps(f.scope)


def test_reuse_health_counts_runs_and_workflows_separately(db) -> None:
    warned_twice = _run_calls("twice", 2, "s")
    warned_twice[0] = warned_twice[-1] | {"status": "completed"}  # the same warning on both calls
    rows = warned_twice + _run_calls("single", 1, "s")  # only its resume call was ingested: still resumed
    rows += [r for i in range(8) for r in _run_calls(f"ok-{i}", 2)]
    other = [r | {"workflow": "v"} for r in _run_calls("v-1", 2, "s") + _run_calls("v-2", 2)]
    put(db, "run_calls", rows + other)
    by_workflow = {f.scope["workflow"]: f for f in reuse_health(db)}
    w, v = by_workflow["w"], by_workflow["v"]
    assert (w.affected, w.total, len(w.evidence)) == (2, 10, 2)  # one per run, however many calls warned
    assert (v.affected, v.total) == (1, 2)


def test_reuse_health_is_quiet_without_warnings(db) -> None:
    put(db, "run_calls", [r for i in range(20) for r in _run_calls(f"ok-{i}", 2)])
    assert reuse_health(db) == []


def test_cost_and_time_share_and_token_growth(db) -> None:
    put(db, "steps", [{"step": "slow", "duration_ms": 900}, {"step": "fast", "duration_ms": 100}])
    calls = [{"step": "a", "cost_usd": 0.9, "template_version": "1", "input_tokens": 100}] * 5
    calls += [{"step": "b", "cost_usd": 0.1}] + [
        {"step": "a", "template_version": "2", "input_tokens": 250}
    ] * 5
    put(db, "calls", calls)
    by_metric = {f.metric["name"]: f for f in cost_latency(db)}
    assert by_metric["time_share"].scope["step"] == "slow"
    assert by_metric["cost_share"].metric["value"] == pytest.approx(4.5 / 4.6)
    growth = by_metric["median_input_tokens"]
    assert growth.scope["prompt_version"] == "2" and growth.severity == "medium"
    assert growth.details["boundary"] == {"field": "prompt_version", "from": "1", "to": "2"}


def test_gpu_thrash_quiet_without_signals(db) -> None:
    put(db, "calls", [{"lease_wait_ms": 10, "gpu_unloaded": False}] * 5)
    assert gpu_thrash(db) == []


def test_selection_health_branches(db) -> None:
    scores = [{"kind": "score", "scorer": "q", "value": None}] * 5 + [
        {"kind": "score", "scorer": "q", "value": 0.7}
    ] * 5
    gates = [{"kind": "gate", "gate": "len", "passed": False}] * 6 + [
        {"kind": "gate", "gate": "len", "passed": True}
    ] * 4
    thin = json.dumps([["a", 0.81], ["b", 0.8]])
    decisions = [{"kind": "decision", "fallback_used": True, "ranked": thin}] * 4
    decisions += [
        {
            "kind": "decision",
            "fallback_used": False,
            "ranked": json.dumps([{"id": "a", "total": 0.9}, {"id": "b", "total": 0.1}]),
        }
    ]
    judged = []
    for c in range(5):
        judged += [
            {"kind": "score", "scorer": "j1", "candidate_id": f"c{c}", "value": 0.9},
            {"kind": "score", "scorer": "j2", "candidate_id": f"c{c}", "value": 0.1},
        ]
    put(db, "selections", scores + gates + decisions + judged)
    metrics = {f.metric["name"]: f for f in selection_health(db)}
    assert set(metrics) == {
        "none_score_rate",
        "gate_rejection_rate",
        "fallback_rate",
        "low_margin_rate",
        "judge_disagreement_rate",
    }
    assert metrics["none_score_rate"].category == "reliability" and metrics["none_score_rate"].affected == 5
    assert metrics["gate_rejection_rate"].scope["gate"] == "len"
    assert metrics["low_margin_rate"].affected == 4
    assert metrics["judge_disagreement_rate"].affected == 5


def test_zero_scores_next_to_low_scores_are_not_a_spike(db) -> None:
    put(
        db,
        "selections",
        [{"kind": "score", "scorer": "q", "value": v} for v in [0.0] * 5 + [0.1] * 5 + [0.8] * 40],
    )
    assert selection_health(db) == []


def _scale_scores(zero: dict, levels: dict[float, int]) -> list[dict]:
    """A 1-5 judge mapped to 0-1: 10 zeros like `zero`, then `levels` {value: count}."""
    rows = [{"kind": "score", "scorer": "clean", "value": 0.0, **zero}] * 10
    return rows + [
        {"kind": "score", "scorer": "clean", "value": v} for v, n in levels.items() for _ in range(n)
    ]


SCALE = {0.25: 2, 0.5: 10, 0.75: 18, 1.0: 10}


def test_explained_zeros_on_a_discrete_scale_are_floor_scores(db) -> None:
    # 0004: a 1-5 judge that answers "1" with a reason and a confidence is not a failed scorer
    reason = "The image is a painting, which is not a clean, well-composed picture"
    put(db, "selections", _scale_scores({"reason": reason, "confidence": 0.9}, SCALE))
    (f,) = selection_health(db)
    assert (f.metric["name"], f.category, f.affected, f.total, f.severity) == (
        "floor_score_rate",
        "quality",
        10,
        50,
        "medium",
    )
    assert "lowest possible" in f.title and "failed" not in f.title
    assert f.details["sample_reasons"] == [reason] * 3


@pytest.mark.parametrize(
    "zero",
    [
        {},  # no reason, no confidence
        {"reason": "fine", "confidence": None},
        {"reason": "", "confidence": 0.9},
        {"reason": "ok", "confidence": 0.9, "error": "ValueError: no score"},
        {"reason": "Could not parse the answer", "confidence": 0.9},
    ],
)
def test_zeros_without_the_judges_answer_are_possibly_failed(db, zero) -> None:
    put(db, "selections", _scale_scores(zero, SCALE))
    (f,) = selection_health(db)
    assert (f.metric["name"], f.affected) == ("zero_score_rate", 10) and "failed scores" in f.title


def test_zeros_next_to_the_next_level_up_are_not_a_spike(db) -> None:
    # on a 1-5 scale nothing falls in (0, 0.2): compare the zeros with the next level, 0.25
    put(db, "selections", _scale_scores({}, {0.25: 8, 0.5: 12, 1.0: 20}))
    assert selection_health(db) == []


def test_human_override(db) -> None:
    rows = [{"kind": "human_gate", "step": "s", "gate": "approve", "decision": "reject"}] * 3
    rows += [{"kind": "human_gate", "step": "s", "gate": "approve", "decision": "approve"}] * 3
    put(db, "selections", rows)
    (f,) = human_override(db)
    assert f.metric["value"] == 0.5 and f.severity == "high" and f.scope["gate"] == "approve"
    put(db, "selections", [{"kind": "human_gate", "step": "t", "decision": "reject"}] * 4)
    assert len(human_override(db)) == 1  # fewer than 5 decisions: not enough to judge


def test_human_override_is_per_workflow_and_case_insensitive(db) -> None:
    decisions = ["Rejected", "EDITED", "overruled", "approved", "approved"]
    rows = [{"kind": "human_gate", "step": "g", "gate": "g", "decision": d} for d in decisions]
    put(db, "selections", rows + [r | {"workflow": "other"} for r in rows])
    found = human_override(db)
    assert sorted(f.scope["workflow"] for f in found) == ["other", "w"]
    assert all((f.affected, f.total) == (3, 5) for f in found)


def test_rejected_gate_records_alone_are_not_revisions(db) -> None:
    rejected = json.dumps(["rejected"])
    put_flow_steps(
        db,
        [{"step": "review", "kind": "gate", "item": str(i), "attempt_statuses": rejected} for i in range(9)],
    )
    assert human_override(db) == []


def test_human_override_ignores_gate_pauses(db) -> None:
    # hone-flow writes one gate span when the gate pauses (no decision) and one per review decision
    rows = [{"kind": "human_gate", "step": "g", "gate": "g", "decision": "rejected"}] * 3
    rows += [{"kind": "human_gate", "step": "g", "gate": "g", "decision": "approved"}] * 7
    rows += [{"kind": "human_gate", "step": "g", "gate": "g", "decision": None}] * 30
    put(db, "selections", rows)
    (f,) = human_override(db)
    assert (f.affected, f.total, f.metric["value"]) == (3, 10, 0.3)


def put_flow_steps(db: AnalyticsDB, rows: list[dict]) -> None:
    with sqlite3.connect(db.path) as con:
        for n, row in enumerate(rows):
            full = {
                "run_id": f"run-{n}",
                "workflow": "w",
                "start_time": f"2026-09-01T00:00:{n % 60:02d}.000Z",
                "attempt": 1,
            }
            full |= row
            assert set(full) <= set(FLOW_STEPS)
            names = ", ".join(full)
            con.execute(
                f"INSERT INTO flow_steps ({names}) VALUES ({', '.join('?' * len(full))})", list(full.values())
            )
    con.close()


def test_human_override_counts_reject_and_revise_cycles_from_flow_steps(db) -> None:
    lyrics = [{"step": "lyrics", "item": str(i), "attempt_statuses": "[]"} for i in range(6)]
    lyrics += [
        {"step": "lyrics", "item": "6", "attempt_statuses": json.dumps(["rejected", "rejected"])},
        {"step": "lyrics", "item": "7", "attempt_statuses": json.dumps(["failed", "rejected"])},
        {"step": "lyrics", "item": "8", "attempt_statuses": json.dumps(["failed"])},  # a retry, not a review
        {"step": "lyrics", "item": "9", "attempt_statuses": json.dumps(["rejected"])},
    ]
    gates = [
        {"step": "review", "kind": "gate", "item": "6", "attempt_statuses": json.dumps(["rejected"])}
    ] * 3
    # not counted: steps that have not run (attempt 0: pending, skipped, blocked) and fork copies
    lyrics += [{"step": "lyrics", "item": "p", "attempt": 0, "attempt_statuses": "[]"}] * 5
    rejected_once = json.dumps(["rejected"])
    lyrics += [{"step": "lyrics", "item": "r", "reused_from": "run-0", "attempt_statuses": rejected_once}] * 5
    put_flow_steps(db, lyrics + gates)
    put(db, "steps", [{"run_id": "run-6", "step": "lyrics", "item": "6", "span_id": "00000000000000aa"}])
    (f,) = human_override(db)
    assert (f.metric["name"], f.affected, f.total) == ("revision_rate", 3, 10)
    assert f.details == {"revisions": 4, "max_revisions": 2}
    assert f.scope["step"] == "lyrics" and "4 reject-and-revise cycles" in f.title
    # evidence: the step's span when it was ingested, else run/step/item
    assert set(f.evidence) == {"00000000000000aa", "run-7/lyrics/7", "run-9/lyrics/9"}


def _render_check(run: str, rounds: int) -> list[dict]:
    """An automated reviewer's decisions on one item: `rounds` rejections, then an approval."""
    gate = {"kind": "human_gate", "run_id": run, "item": "01", "step": "check", "gate": "check"}
    decisions = ["rejected"] * rounds + ["approved"]
    return [gate | {"decision": d, "actor": "render-check", "actor_kind": "automated"} for d in decisions]


def test_automated_gate_decisions_are_not_decisions_by_people(db) -> None:
    # 0005: a script behind the gate API rejecting crashes in round one is the design working
    rows = [r for n in range(6) for r in _render_check(f"run-{n}", rounds=2 if n < 2 else 1 if n < 4 else 0)]
    put(db, "selections", rows)
    (f,) = human_override(db)
    assert (f.metric["name"], f.scope["gate"], f.affected, f.total) == (
        "automated_rework_rate",
        "check",
        4,
        6,
    )
    assert "automated" in f.title and "by people" not in f.title and f.severity == "medium"
    assert f.details == {
        "rounds_until_approved": {"0": 2, "1": 2, "2": 2},
        "mean_rounds": 1.0,
        "max_rounds": 2,
    }
    people = [{"kind": "human_gate", "step": "check", "gate": "check", "decision": "rejected"}] * 5
    put(db, "selections", people + [r | {"actor_kind": "person"} for r in people])
    by_metric = {f.metric["name"]: f for f in human_override(db)}
    assert by_metric["override_rate"].total == 10  # people only; a missing actor kind is a person


def test_items_sent_back_only_by_automated_reviewers_are_not_review_revisions(db) -> None:
    rejected = json.dumps(["rejected"])
    automated = json.dumps(
        [
            {"decision": "rejected", "actor_kind": "automated"},
            {"decision": "approved", "actor_kind": "automated"},
        ]
    )
    mixed = json.dumps([{"decision": "rejected", "actor_kind": "automated"}, {"decision": "rejected"}])
    rows = [{"run_id": f"r{i}", "step": "code", "item": "01", "attempt_statuses": rejected} for i in range(6)]
    rows += [
        {"run_id": f"r{i}", "step": "check", "item": "01", "kind": "gate", "review_decisions": automated}
        for i in range(5)
    ]
    rows += [{"run_id": "r5", "step": "check", "item": "01", "kind": "gate", "review_decisions": mixed}]
    put_flow_steps(db, rows)
    assert human_override(db) == []  # 1 of 6 items revised by a person: below the rate
    put_flow_steps(db, [r | {"run_id": f"p{n}"} for n, r in enumerate(rows[:3])])  # no gate records: people
    (f,) = human_override(db)
    assert (f.metric["name"], f.affected, f.total) == ("revision_rate", 4, 9)


def test_revisions_below_the_rate_are_quiet(db) -> None:
    rows = [{"step": "lyrics", "item": str(i), "attempt_statuses": "[]"} for i in range(10)]
    rows += [{"step": "lyrics", "item": "x", "attempt_statuses": json.dumps(["rejected"])}]
    put_flow_steps(db, rows)
    assert human_override(db) == []


@pytest.mark.parametrize(
    ("model", "expected"),
    [("gemma4-12b:latest", "gemma4"), ("ollama/Qwen3:8b", "qwen3"), ("gpt-4o", "gpt"), ("llama3", "llama3")],
)
def test_family(model: str, expected: str) -> None:
    assert family(model) == expected


def test_seed_parameter_silences_the_unseeded_smell(db) -> None:
    calls = [{"step": "s", "model": "m", "seed": i, "input_sha": "same"} for i in range(5)]
    put(db, "calls", calls)
    assert [f.metric["name"] for f in setup_smells(db)] == ["unseeded_repeat_rate"]
    put(db, "steps", [{"step": "s", "params": json.dumps({"seed": 1})}, {"step": "s", "params": "[]"}])
    assert setup_smells(db) == []


def test_a_run_that_records_a_seed_silences_the_unseeded_smell(db) -> None:
    # best-of-N inside a seeded hone-flow run: the variation seeds derive from the run seed (0003)
    calls = [
        {"run_id": r, "step": "s", "model": "m", "seed": i, "input_sha": r} for r in "abcd" for i in (1, 2, 3)
    ]
    put(db, "calls", calls)
    assert [f.affected for f in setup_smells(db)] == [12]
    put(db, "run_calls", [{"run_id": "a", "seed": 7}])  # `hone.flow.seed` on the run span
    put(db, "steps", [{"run_id": "b", "step": "s", "seed": 11}])  # `hone.flow.seed` on the step span
    put_flow_steps(db, [{"run_id": "c", "step": "s", "run_seed": 0}])  # the manifest's seed (`flow:`)
    (f,) = setup_smells(db)
    assert (f.affected, f.total) == (3, 12) and "records a seed" in f.title
    put_flow_steps(db, [{"run_id": "d", "step": "s", "run_seed": 3}])
    assert setup_smells(db) == []


def test_error_rate_regression_at_a_model_boundary(db) -> None:
    put(db, "calls", [{"step": "s", "model": "old", "status": "ok", "latency_ms": 100}] * 40)
    put(db, "calls", [{"step": "s", "model": "new", "status": "error", "latency_ms": 100}] * 20)
    put(db, "calls", [{"step": "s", "model": "new", "status": "ok", "latency_ms": 100}] * 20)
    (f,) = regression(db)
    assert f.metric["name"] == "error_rate" and f.metric["value"] == 0.5 and f.metric["baseline"] == 0
    assert f.details["boundary"]["field"] == "model" and "model old -> new" in f.title
    assert f.scope["model"] == "new" and f.total == 80


def test_balanced_two_step_workflow_is_not_a_cost_finding(db) -> None:
    put(db, "steps", [{"step": "a", "duration_ms": 600}, {"step": "b", "duration_ms": 400}])
    assert cost_latency(db) == []


def test_retry_events_count(db) -> None:
    put(
        db,
        "calls",
        [{"step": "s", "model": "m", "retries": 2}] * 3 + [{"step": "s", "model": "m", "retries": 0}] * 7,
    )
    (f,) = failure_rate(db)
    assert f.metric["name"] == "retry_rate" and f.affected == 3


def _latency_calls(ms_before: float | None, ms_after: float, n: int = 40, **extra) -> list[dict]:
    before = [
        {
            "step": "s",
            "model": "m",
            "template_version": "1",
            "status": "ok",
            "latency_ms": None if ms_before is None else ms_before + i,
        }
        for i in range(n)
    ]
    after = [
        {
            "step": "s",
            "model": "m",
            "template_version": "2",
            "status": "ok",
            "latency_ms": ms_after + i,
            **extra,
        }
        for i in range(n)
    ]
    return before + after


def test_regression_needs_enough_runs(db) -> None:
    put(db, "calls", _latency_calls(100, 300, n=19))
    assert regression(db) == []


def test_regression_needs_significance(db, monkeypatch) -> None:
    # a 20% slower median, but the distributions overlap heavily: p is about 0.18
    before = [{"step": "s", "model": "m", "template_version": "1", "latency_ms": v} for v in [100, 400] * 10]
    after = [
        {"step": "s", "model": "m", "template_version": "2", "latency_ms": v} for v in [150, 450, 460, 95] * 5
    ]
    put(db, "calls", before + after)
    assert regression(db) == []
    monkeypatch.setattr(changes, "ALPHA", 0.5)  # alpha is the documented knob
    (f,) = regression(db)
    assert f.metric["name"] == "latency_ms_median" and f.metric["p_value"] < 0.5


def test_regression_ignores_missing_latency_zero_baselines_and_unversioned_calls(db) -> None:
    def calls(step: str, version: str, latency: float | None) -> list[dict]:
        return [{"step": step, "model": "m", "template_version": version, "latency_ms": latency}] * 30

    put(db, "calls", calls("a", "1", None) + calls("a", "2", 500))  # nothing measured before
    put(db, "calls", calls("b", "1", 0.0) + calls("b", "2", 500))  # a zero baseline
    put(db, "calls", [{"step": "c", "model": "m", "latency_ms": 10 * i} for i in range(60)])  # no version
    assert regression(db) == []


def test_regression_ignores_replays(db) -> None:
    put(db, "calls", _latency_calls(100, 100))
    put(
        db,
        "calls",
        [{"step": "s", "model": "m", "template_version": "2", "latency_ms": 900, "replay_of": "x"}] * 40,
    )
    assert regression(db) == []


def test_error_rate_needs_a_real_rise(db) -> None:
    calls = [{"step": "s", "model": "old", "status": "ok"}] * 20 + [
        {"step": "s", "model": "new", "status": "ok"}
    ] * 19
    put(db, "calls", [*calls, {"step": "s", "model": "new", "status": "error"}])
    assert regression(db) == []


def test_code_version_and_score_regressions(db) -> None:
    steps = [{"step": "s", "step_version": "1", "duration_ms": 100 + i, "status": "ok"} for i in range(30)]
    steps += [{"step": "s", "step_version": "2", "duration_ms": 300 + i, "status": "ok"} for i in range(30)]
    put(db, "steps", steps)
    calls = [{"run_id": f"r{i}", "step": "g", "template_version": "1" if i < 30 else "2"} for i in range(60)]
    put(db, "calls", calls)
    scores = [
        {"run_id": f"r{i}", "kind": "score", "scorer": "q", "value": 0.8 if i < 30 else 0.5}
        for i in range(60)
    ]
    put(db, "selections", scores)
    found = {f.metric["name"]: f for f in regression(db)}
    assert found["latency_ms_median"].details["boundary"]["field"] == "code_version"
    assert "step version 1 -> 2" in found["latency_ms_median"].title
    score = found["mean_score"]
    assert (score.metric["baseline"], score.metric["value"]) == (pytest.approx(0.8), pytest.approx(0.5))
    assert score.scope == {**score.scope, "scorer": "q", "prompt_version": "2"} and score.severity == "high"
