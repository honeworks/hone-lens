"""hone-flow runs through hone-flow's public read API (extra `flow`).

- `FlowRuns(storage, name)`: a record source over `fk.open_runs(storage, name)`, so it reads run folders
  on any storage hone-flow supports (local, S3). Besides the spans of every run it yields **step records**
  (`step_records(since=)`), which `Workspace.ingest` stores in the `flow_steps` table.
- `FlowStepRerunner(workflow)`: the `StepRerunner` port (current.md §6) as a hone-flow fork.

hone-flow is imported only when `FlowRuns` opens the storage itself. Pass `history=` (anything with
hone-flow's `runs(updated_since=)` / `open_run(run_id)`, such as `hone_lens.testing.FakeFlowRuns`) to read
without hone-flow installed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from hone_lens.errors import SourceError
from hone_lens.mapping import as_dict, as_list, canonical_time, parse_time
from hone_lens.ports import get

INSTALL = "reading hone-flow runs needs hone-flow: pip install 'hone-lens[flow]'"


class FlowRuns:
    """The runs of hone-flow workflow `name` under `storage` (a folder or URL such as `s3://bucket/prefix`).

    >>> source = FlowRuns("s3://hone-flow/projects/oneshotstudio", "song_video")  # doctest: +SKIP
    >>> ws.ingest(source)  # doctest: +SKIP
    >>> ws.ingest("flow:s3://hone-flow/projects/oneshotstudio/song_video")  # the same  # doctest: +SKIP

    Incremental: the cursor is the newest manifest `updated_at` seen; the next read asks hone-flow for the
    runs updated at or after it (`runs(updated_since=...)`), reads their spans (known span ids are skipped)
    and replaces their step records.
    """

    def __init__(self, storage: str, name: str, *, history: Any = None) -> None:
        self.storage = str(storage).rstrip("/")
        self.workflow = name
        self.name = f"flow:{self.storage}/{name}"
        self.history: Any = history if history is not None else _open_runs(self.storage, name)
        self._read: tuple[str | None, list[dict[str, Any]]] | None = None  # (since, step records) of read_new

    def spans(self, *, since: str | None = None) -> list[dict[str, Any]]:
        """The spans of every run, or those starting at or after `since` (ISO-8601)."""
        start = parse_time(since) if since is not None else None
        return [
            span
            for summary in self.history.runs(updated_since=since)
            for span in self._run(summary).spans()
            if start is None or parse_time(span["start_time"]) >= start
        ]

    def read_new(self, cursor: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """The spans of the runs updated since `cursor["since"]` (`{}`: every run) and the next cursor.

        The step records of the same runs are read in the same pass and kept for the `step_records` call
        `Workspace.ingest` makes next (so each run is listed and opened once per ingest).
        """
        since = cursor.get("since")
        summaries = list(self.history.runs(updated_since=since))
        runs = [(summary, self._run(summary)) for summary in summaries]
        spans = [span for _, run in runs for span in run.spans()]
        records = [row for summary, run in runs for row in _step_rows(summary, run)]
        self._read = (since, records)
        times = [str(get(s, "updated_at")) for s in summaries]
        newest = max(times, key=parse_time) if times else since
        return spans, ({"since": newest} if newest else {})

    def step_records(self, *, since: str | None = None) -> list[dict[str, Any]]:
        """One `flow_steps` row per (run, step, item) of the runs updated at or after `since`: the ones
        `read_new(since)` just read, else read now."""
        read, self._read = self._read, None
        if read is not None and read[0] == since:
            return read[1]
        return [
            row
            for summary in self.history.runs(updated_since=since)
            for row in _step_rows(summary, self._run(summary))
        ]

    def _run(self, summary: Any) -> Any:
        return self.history.open_run(get(summary, "run_id"))


def _step_rows(summary: Any, run: Any) -> list[dict[str, Any]]:
    seed = run_seed(run)
    return [step_row(summary, record, seed) for record in run.steps()]


def run_seed(run: Any) -> int | None:
    """The run's seed from its manifest (`run.manifest["seed"]`), `None` when not recorded."""
    seed = as_dict(get(run, "manifest")).get("seed")
    return seed if isinstance(seed, int) else None


def step_row(summary: Any, record: Any, seed: int | None = None) -> dict[str, Any]:
    """A `flow_steps` row from a hone-flow `RunSummary`, one of the run's `StepRecord`s and the run's
    seed."""
    attempts = [as_dict(a) for a in as_list(get(record, "attempts"))]
    reviews = [as_dict(r) for r in as_list(get(record, "reviews"))]
    error = get(record, "error")
    started = get(record, "started_at") or get(summary, "created_at")
    return {
        "run_id": get(summary, "run_id"),
        "workflow": get(summary, "workflow"),
        "step": get(record, "step"),
        "item": get(record, "item"),
        "kind": get(record, "kind"),
        "status": get(record, "status"),
        "attempt": get(record, "attempt"),
        "attempts": len(attempts),
        "attempt_statuses": json.dumps([a.get("status") for a in attempts]),
        "reused_from": get(record, "reused_from"),
        "fork_of": get(summary, "fork_of"),
        "run_seed": seed,
        # review notes are free text people wrote: only whether there is one is kept
        "review_decisions": json.dumps(
            [{k: r.get(k) for k in ("decision", "actor", "actor_kind", "at", "attempt")} for r in reviews]
        ),
        "review_note_present": int(bool(get(record, "review_note"))),
        "error": as_dict(error).get("message") if error else None,
        "start_time": canonical_time(started) if started else None,
        "duration_ms": get(record, "duration_ms"),
    }


def flow_source(target: str) -> FlowRuns:
    """`FlowRuns` from the `flow:` source string's target: `<storage>/<workflow>`, the last path segment
    being the workflow name (`s3://hone-flow/projects/oneshotstudio/song_video`)."""
    storage, _, name = target.rstrip("/").rpartition("/")
    if not storage or not name or storage.endswith(":/"):
        raise SourceError(
            f"flow source {target!r} needs a storage and a workflow name: 'flow:<storage>/<workflow>', "
            "e.g. 'flow:flows/song_video' or 'flow:s3://bucket/prefix/song_video'"
        )
    return FlowRuns(storage, name)


class FlowStepRerunner:
    """`StepRerunner` (current.md §6) for a hone-flow `Workflow`: a fork of the run that reruns `step` and
    everything downstream for `items`, with `params` merged over the source run's params; the rest is
    copied. Returns the fork's run id.

    >>> rerunner = FlowStepRerunner(wf)  # doctest: +SKIP
    >>> rerunner.rerun("20260927T140311Z-3f9a1c", "lyrics", ["01"], {"style": "noir"})  # doctest: +SKIP
    ['20260927T151200Z-7b2e90']
    """

    def __init__(self, workflow: Any) -> None:
        self.workflow = workflow

    def rerun(self, run_id: str, step: str, items: Sequence[str], params: Mapping[str, Any]) -> list[str]:
        fork = self.workflow.open_run(run_id).fork(refresh=(step,), items=items, params=params)
        return [str(fork.run_id)]


def _open_runs(storage: str, name: str) -> Any:
    try:
        import hone_flow  # noqa: PLC0415 - optional extra, imported only when it is used
    except ImportError as e:
        raise SourceError(INSTALL) from e
    return hone_flow.open_runs(storage, name)
