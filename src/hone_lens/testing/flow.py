"""`FakeFlowRuns`: hone-flow's read API (`fk.open_runs(storage, name)`) over in-memory runs, without
importing hone-flow. Use it with `FlowRuns(..., history=fake)` and as the workflow of `FlowStepRerunner`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from hone_lens.mapping import parse_time

# The fields of hone-flow's `StepRecord` that hone-lens reads, with the values a step that ran once has
# (a step that never ran, `NOT_RUN`, has attempt 0).
STEP_DEFAULTS: dict[str, Any] = {
    "item": None,
    "kind": "step",
    "status": "done",
    "version": "1",
    "source_hash": "",
    "attempt": 1,
    "attempts": [],
    "labels": [],
    "reused_from": None,
    "reviews": [],
    "review_note": None,
    "params": {},
    "error": None,
    "started_at": None,
    "ended_at": None,
    "duration_ms": None,
}
NOT_RUN = ("pending", "skipped", "blocked")


@dataclass
class FakeRun:
    """One run: a `RunSummary` and a `Run` in one object (`steps()`, `spans()`, `manifest`, `fork()`)."""

    history: FakeFlowRuns = field(repr=False)
    run_id: str
    workflow: str
    status: str
    created_at: str
    updated_at: str
    fork_of: str | None
    records: list[dict[str, Any]]
    seed: int | None = None

    @property
    def manifest(self) -> dict[str, Any]:
        keys = ("run_id", "workflow", "status", "created_at", "updated_at")
        fork = {"run_id": self.fork_of} if self.fork_of else None
        seed = {} if self.seed is None else {"seed": self.seed}
        return {"format_version": "1", **{k: getattr(self, k) for k in keys}, "fork_of": fork, **seed}

    def steps(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.records]

    def spans(self) -> list[dict[str, Any]]:
        """`hone.flow.run` / `.step` / `.gate` spans as hone-flow writes them to `spans.jsonl`."""
        run_span = _span(self, "run", "hone.flow.run", None, self.created_at, _run_attributes(self))
        spans = [run_span]
        for r in self.records:
            name = "hone.flow.gate" if r["kind"] == "gate" else "hone.flow.step"
            at = r["started_at"] or self.created_at
            key = f"{r['step']}/{r['item']}"
            spans.append(_span(self, key, name, run_span["span_id"], at, _step_attributes(self, r)))
            for n, review in enumerate(r["reviews"]):
                decision = _review_attributes(self, r, review)
                spans.append(_span(self, f"{key}/review/{n}", "hone.flow.gate", None, review["at"], decision))
        return spans

    def fork(
        self,
        refresh: Sequence[str] = (),
        *,
        items: Sequence[str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> FakeRun:
        """A new run beside this one, like hone-flow's `run.fork(refresh, items=, params=)`: the `refresh`
        steps and everything downstream rerun (status `done`, attempt 1), the rest is copied with
        `reused_from`; only `items` when given. Recorded in `history.forks`. The fake's workflow is a chain:
        a step is downstream of every step recorded before it."""
        self.history.forks.append(
            {"run_id": self.run_id, "refresh": tuple(refresh), "items": items, "params": params}
        )
        order = list(dict.fromkeys(r["step"] for r in self.records))
        first = min((order.index(s) for s in refresh if s in order), default=len(order))
        rerun = set(order[first:])  # the refreshed steps and everything after them
        kept = [r for r in self.records if items is None or r["item"] is None or r["item"] in items]
        records: list[dict[str, Any]] = [
            {"step": r["step"], "item": r["item"], "kind": r["kind"]}
            if r["step"] in rerun
            else {**r, "reused_from": self.run_id, "attempts": [], "reviews": []}
            for r in kept
        ]
        new_id = f"{self.run_id}-fork-{len(self.history.forks)}"
        return self.history.add_run(
            new_id, records, workflow=self.workflow, fork_of=self.run_id, seed=self.seed
        )


class FakeFlowRuns:
    """In-memory runs with hone-flow's read API: `runs(updated_since=)` and `open_run(run_id)`.

    >>> fake = FakeFlowRuns()
    >>> run = fake.add_run("r1", [{"step": "ideas", "item": "01"}, {"step": "review", "item": "01",
    ...                    "kind": "gate", "status": "awaiting_review"}], status="awaiting_review")
    >>> [s.run_id for s in fake.runs()]
    ['r1']

    `asked` records every `updated_since` passed to `runs`; `forks` every fork made.
    """

    def __init__(self) -> None:
        self.by_id: dict[str, FakeRun] = {}
        self.asked: list[str | None] = []
        self.forks: list[dict[str, Any]] = []

    def add_run(
        self,
        run_id: str,
        steps: Sequence[Mapping[str, Any]],
        *,
        workflow: str = "song_ideas",
        status: str = "completed",
        created_at: str | None = None,
        updated_at: str | None = None,
        fork_of: str | None = None,
        seed: int | None = None,
    ) -> FakeRun:
        """Add (or replace, as a resume would) a run. `steps` are `StepRecord` fields (`step` required; the
        rest default to a step that ran once, `STEP_DEFAULTS`, or never ran for a `NOT_RUN` status).
        Times default to one minute apart. `seed` is the manifest's run seed (left out when `None`)."""
        default = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=len(self.by_id))
        created = created_at or default.isoformat()
        records = [{**STEP_DEFAULTS, "attempt": 0 if s.get("status") in NOT_RUN else 1, **s} for s in steps]
        run = FakeRun(self, run_id, workflow, status, created, updated_at or created, fork_of, records, seed)
        self.by_id[run_id] = run
        return run

    def runs(self, *, updated_since: str | None = None) -> list[FakeRun]:
        """Runs whose `updated_at` is at or after `updated_since`, newest first (as hone-flow lists them)."""
        self.asked.append(updated_since)
        since = parse_time(updated_since) if updated_since is not None else None
        found = [r for r in self.by_id.values() if since is None or parse_time(r.updated_at) >= since]
        return sorted(found, key=lambda r: (r.created_at, r.run_id), reverse=True)

    def open_run(self, run_id: str) -> FakeRun:
        try:
            return self.by_id[run_id]
        except KeyError:
            raise KeyError(f"no run {run_id!r}; known: {sorted(self.by_id)}") from None


def _hex(*parts: str, n: int) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:n]


def _span(
    run: FakeRun, key: str, name: str, parent: str | None, at: str, attributes: dict[str, Any]
) -> dict[str, Any]:
    failed = attributes.get("hone.flow.status") == "failed"
    return {
        "trace_id": _hex(run.run_id, "trace", n=32),
        "span_id": _hex(run.run_id, key, n=16),
        "parent_span_id": parent,
        "name": name,
        "kind": "internal",
        "start_time": at,
        "end_time": at,
        "status": {"code": "error", "message": "step failed"} if failed else {"code": "ok", "message": ""},
        "attributes": {"hone.schema_version": "1", "hone.run_id": run.run_id, **attributes},
        "events": [],
        "resource": {"hone.package": "hone-flow"},
        "links": [],
    }


def _run_attributes(run: FakeRun) -> dict[str, Any]:
    attributes = {"hone.flow.workflow": run.workflow, "hone.flow.status": run.status}
    return attributes | ({"hone.flow.fork_of": run.fork_of} if run.fork_of else {})


def _step_attributes(run: FakeRun, record: Mapping[str, Any]) -> dict[str, Any]:
    attributes = {
        "hone.step": record["step"],
        "hone.flow.workflow": run.workflow,
        "hone.flow.step_version": record["version"],
        "hone.flow.status": record["status"],
        "hone.flow.attempt": record["attempt"],
    }
    if record["item"] is not None:
        attributes["hone.item"] = record["item"]
    if record["reused_from"]:
        attributes["hone.flow.reused_from"] = record["reused_from"]
    return attributes


def _review_attributes(run: FakeRun, record: Mapping[str, Any], review: Mapping[str, Any]) -> dict[str, Any]:
    kind = {"hone.flow.gate.actor_kind": review["actor_kind"]} if review.get("actor_kind") else {}
    return _step_attributes(run, record) | {
        "hone.flow.gate.decision": review["decision"],
        "hone.flow.gate.actor": review.get("actor", "reviewer"),
        **kind,
    }
