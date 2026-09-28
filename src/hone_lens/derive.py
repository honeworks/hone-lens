"""Derived analytics rows (`calls`, `steps`, `selections`, `outputs`, `run_calls`) from the spans of one
trace.

Rows are rebuilt per trace on every ingest that touches the trace, so a workflow name recorded in one
store (`hone.flow.run`) reaches model calls recorded in another. Plain OTel GenAI traces have no
`hone.*` fields: the workflow is the resource `service.name`, the step is the parent span's name and the
run is the trace.

hone-flow steps (`hone.flow.step` spans and a gate's own `hone.flow.gate` span): `status` is
`hone.flow.status` (`done`, `failed`, `skipped`, `blocked`, `awaiting_review`, `interrupted`, `pending`);
`executed` is 1 only for a step that ran in this call and finished (`done` / `failed`, not copied by a
fork), so durations and failure rates count real work only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from hone_lens.mapping import (
    as_dict,
    finish_reason,
    is_model_call,
    json_value,
    messages_text,
    output_text,
    parse_time,
)

_CONTEXT = {
    "span_id": "TEXT PRIMARY KEY",
    "trace_id": "TEXT NOT NULL",
    "run_id": "TEXT",
    "workflow": "TEXT",
    "step": "TEXT",
    "item": "TEXT",
    "start_time": "TEXT",
}
# table -> column -> SQL type; the single definition of the derived tables (store.py creates them).
COLUMNS: dict[str, dict[str, str]] = {
    "calls": {
        **_CONTEXT,
        "model": "TEXT",
        "provider": "TEXT",
        "scorer": "TEXT",
        "template_id": "TEXT",
        "template_version": "TEXT",
        "sections": "TEXT",  # JSON list of {id, version, start, end, sha256}
        "structured_path": "TEXT",
        "retries": "INTEGER",  # `retry` span events
        "finish_reason": "TEXT",
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "context_limit": "INTEGER",
        "temperature": "REAL",
        "seed": "INTEGER",
        "input_sha": "TEXT",
        "latency_ms": "REAL",
        "cost_usd": "REAL",
        "lease_wait_ms": "REAL",
        "vram_after_mb": "REAL",
        "gpu_unloaded": "INTEGER",
        "status": "TEXT",
        "error": "TEXT",
        "replay_of": "TEXT",
    },
    "steps": {
        **_CONTEXT,
        "step_version": "TEXT",
        "source_hash": "TEXT",
        "status": "TEXT",  # hone.flow.status (span status code when absent)
        "executed": "INTEGER",  # 1: ran and finished in this call (done / failed, not reused)
        "attempt": "INTEGER",
        "reused_from": "TEXT",  # source run id of a step a fork copied
        "fork_of": "TEXT",  # source run id when this run is a fork
        "params": "TEXT",  # JSON object
        "seed": "INTEGER",  # hone.flow.seed: the step's seed (`ctx.seed`), when recorded
        "deterministic": "INTEGER",
        "duration_ms": "REAL",
        "cpu_utilization": "REAL",
        "memory_peak_mb": "REAL",
        "gpu_peak_mb": "REAL",
        "gpu_utilization": "REAL",
        "error": "TEXT",
    },
    "selections": {
        **_CONTEXT,
        "kind": "TEXT",  # run | generate | score | pairwise | gate | decision (hone.select.*) | human_gate
        "candidate_id": "TEXT",
        "scorer": "TEXT",
        "value": "REAL",
        "confidence": "REAL",
        "reason": "TEXT",  # the judge's reason for a score; "(not captured, N chars)" when only hashed
        "error": "TEXT",
        "gate": "TEXT",
        "passed": "INTEGER",
        "probability": "REAL",
        "decision": "TEXT",
        "actor": "TEXT",
        "actor_kind": "TEXT",  # hone.flow.gate.actor_kind: person | automated (None: a person, older runs)
        "winner_id": "TEXT",
        "fallback_used": "INTEGER",
        "escalated": "INTEGER",
        "ranked": "TEXT",  # JSON ids + totals (decision spans)
        "status": "TEXT",
    },
    "outputs": {
        **_CONTEXT,
        "step_span_id": "TEXT",  # the hone.flow.step span (plain OTel: the parent span); span_id is the call
        "model": "TEXT",
        "template_version": "TEXT",
        "text": "TEXT",
        "text_sha": "TEXT",
    },
    # one row per `hone.flow.run` span: one per run / resume / fork call on a run
    "run_calls": {
        **_CONTEXT,
        "status": "TEXT",  # run status at the end of the call (completed, failed, awaiting_review, ...)
        "fork_of": "TEXT",
        "seed": "INTEGER",  # hone.flow.seed: the run's seed, when recorded
        "warnings": "TEXT",  # JSON list of `warning` event attributes (source_changed_version_unchanged)
    },
}
FINISHED = ("done", "failed", "ok", "error")

Span = Mapping[str, Any]


def derive(trace: Sequence[Span]) -> dict[str, list[dict[str, Any]]]:
    """The derived rows of one trace's (normalized) spans, keyed by table."""
    by_id = {s["span_id"]: s for s in trace}
    workflow = _workflow(trace)
    runs = [s for s in trace if s["name"] == "hone.flow.run"]
    fork_of = next(
        (s["attributes"]["hone.flow.fork_of"] for s in runs if s["attributes"].get("hone.flow.fork_of")), None
    )
    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in COLUMNS}
    for span in trace:
        context = _context(span, by_id, workflow)
        name = span["name"]
        if is_model_call(span):
            call = {**context, **_call(span)}
            rows["calls"].append(call)
            text = output_text(span)
            if text and not call["scorer"] and not call["replay_of"]:
                step_span = _step_span(span, by_id)
                rows["outputs"].append({**context, "step_span_id": step_span, **_output(call, text)})
        elif _is_step(span):
            rows["steps"].append({**context, **_step(span), "fork_of": fork_of})
        elif name.startswith("hone.select.") or name == "hone.flow.gate":
            rows["selections"].append({**context, **_selection(span)})
        elif name == "hone.flow.run":
            rows["run_calls"].append({**context, **_run_call(span)})
    return rows


def _is_step(span: Span) -> bool:
    """A workflow step's span: `hone.flow.step`, or a gate's own `hone.flow.gate` span (a `hone.flow.gate`
    span with a review decision is a review, not a step)."""
    name = span["name"]
    return name == "hone.flow.step" or (
        name == "hone.flow.gate" and "hone.flow.gate.decision" not in span["attributes"]
    )


def _workflow(trace: Sequence[Span]) -> str | None:
    for span in trace:
        if "hone.flow.workflow" in span["attributes"]:
            return str(span["attributes"]["hone.flow.workflow"])
    for span in trace:
        if "service.name" in span["resource"]:
            return str(span["resource"]["service.name"])
    return None


def _step_span(span: Span, by_id: Mapping[str, Span]) -> str | None:
    """The nearest `hone.flow.step` ancestor, else the parent span (plain OTel)."""
    parent = by_id.get(span["parent_span_id"] or "")
    while parent is not None and parent["name"] != "hone.flow.step":
        parent = by_id.get(parent["parent_span_id"] or "")
    return parent["span_id"] if parent else span["parent_span_id"]


def _context(span: Span, by_id: Mapping[str, Span], workflow: str | None) -> dict[str, Any]:
    a = span["attributes"]
    parent = by_id.get(span["parent_span_id"] or "")
    step = a.get("hone.step") or (parent["name"] if parent and not a.get("hone.run_id") else None)
    return {
        "span_id": span["span_id"],
        "trace_id": span["trace_id"],
        "run_id": a.get("hone.run_id") or span["trace_id"],
        "workflow": workflow,
        "step": step,
        "item": a.get("hone.item"),
        "start_time": span["start_time"],
    }


def _call(span: Span) -> dict[str, Any]:
    a = span["attributes"]
    model = a.get("hone.models.model_id") or a.get("gen_ai.response.model") or a.get("gen_ai.request.model")
    sections = json_value(a.get("hone.models.prompt.sections"))
    prompt = messages_text(a.get("gen_ai.input.messages"))
    return {
        "model": model,
        "provider": a.get("gen_ai.provider.name"),
        "scorer": a.get("hone.scorer"),
        "template_id": a.get("hone.models.prompt.template_id"),
        "template_version": _text(a.get("hone.models.prompt.template_version")),
        "sections": json.dumps(sections) if isinstance(sections, list) else None,
        "structured_path": a.get("hone.models.structured.path"),
        "retries": sum(as_dict(e).get("name") == "retry" for e in span.get("events", [])),
        "finish_reason": finish_reason(a),
        "input_tokens": a.get("gen_ai.usage.input_tokens"),
        "output_tokens": a.get("gen_ai.usage.output_tokens"),
        "context_limit": a.get("hone.models.context.limit"),
        "temperature": a.get("gen_ai.request.temperature"),
        "seed": a.get("gen_ai.request.seed"),
        "input_sha": text_sha(prompt) if prompt else None,
        "latency_ms": _duration_ms(span),
        "cost_usd": a.get("hone.models.cost_usd"),
        "lease_wait_ms": a.get("hone.models.gpu.lease_wait_ms"),
        "vram_after_mb": a.get("hone.models.gpu.vram_after_mb"),
        "gpu_unloaded": _unloaded(a.get("hone.models.gpu.unloaded")),
        **_status(span),
        "replay_of": a.get("hone.models.replay_of"),
    }


def _unloaded(value: Any) -> int | None:
    """How many models a GPU lease unloaded: hone-models records the list of their names; a bool or a
    number (other producers, synthetic runs) is kept as 0 / 1 / the number."""
    if value is None:
        return None
    if isinstance(value, list | tuple):
        return len(value)  # pyright: ignore[reportUnknownArgumentType]
    return int(value) if isinstance(value, bool | int | float) else 1


def _output(call: Mapping[str, Any], text: str) -> dict[str, Any]:
    return {
        "model": call["model"],
        "template_version": call["template_version"],
        "text": text,
        "text_sha": text_sha(text),
    }


def _step(span: Span) -> dict[str, Any]:
    a = span["attributes"]
    params = json_value(a.get("hone.flow.params"))
    span_status = _status(span)
    status = a.get("hone.flow.status") or span_status["status"]
    reused_from = a.get("hone.flow.reused_from")
    return {
        "step_version": _text(a.get("hone.flow.step_version")),
        "source_hash": a.get("hone.flow.source_hash"),
        "status": status,
        "executed": int(status in FINISHED and not reused_from),
        "attempt": a.get("hone.flow.attempt"),
        "reused_from": reused_from,
        "params": json.dumps(params) if isinstance(params, dict) else None,
        "seed": a.get("hone.flow.seed"),
        "deterministic": a.get("hone.flow.deterministic"),
        "duration_ms": _duration_ms(span),
        "cpu_utilization": a.get("system.cpu.utilization.mean"),
        "memory_peak_mb": a.get("system.memory.usage.peak_mb"),
        "gpu_peak_mb": a.get("hone.flow.gpu.memory.peak_mb"),
        "gpu_utilization": a.get("hone.flow.gpu.utilization.mean"),
        "error": span_status["error"],
    }


def _run_call(span: Span) -> dict[str, Any]:
    a = span["attributes"]
    warnings = [as_dict(e).get("attributes") for e in span["events"] if as_dict(e).get("name") == "warning"]
    return {
        "status": a.get("hone.flow.status") or span["status"]["code"],
        "fork_of": a.get("hone.flow.fork_of"),
        "seed": a.get("hone.flow.seed"),
        "warnings": json.dumps(warnings, default=str) if warnings else None,
    }


def _selection(span: Span) -> dict[str, Any]:
    a = span["attributes"]
    human = span["name"] == "hone.flow.gate"
    status = _status(span)
    candidate = as_dict(json_value(a.get("hone.select.candidate")))
    ranked = json_value(a.get("hone.select.ranked"))
    return {
        "kind": "human_gate" if human else span["name"].removeprefix("hone.select."),
        "candidate_id": a.get("hone.candidate_id") or candidate.get("id"),
        "scorer": a.get("hone.select.scorer") or a.get("hone.scorer"),
        "value": a.get("hone.select.score.value"),
        "confidence": a.get("hone.select.score.confidence"),
        "reason": _reason(a.get("hone.select.score.reason")),
        "error": a.get("hone.select.score.error") or status["error"],
        # hone-flow names a gate span after its step (`hone.step`); older records set `hone.flow.gate`
        "gate": a.get("hone.select.gate")
        or a.get("hone.flow.gate")
        or (a.get("hone.step") if human else None),
        "passed": a.get("hone.select.gate.passed"),
        "probability": a.get("hone.select.gate.probability"),
        "decision": a.get("hone.flow.gate.decision"),
        "actor": a.get("hone.flow.gate.actor"),
        "actor_kind": a.get("hone.flow.gate.actor_kind"),
        "winner_id": a.get("hone.select.winner_id"),
        "fallback_used": a.get("hone.select.fallback_used"),
        "escalated": a.get("hone.select.escalated"),
        "ranked": json.dumps(ranked) if ranked is not None else None,
        "status": status["status"],
    }


def _reason(value: Any) -> str | None:
    """A score's reason; hone-select records only `{sha256, len}` when content capture is off."""
    value = json_value(value)
    if isinstance(value, dict):
        n = as_dict(value).get("len")
        return f"(not captured, {n} chars)" if n else None
    return str(value) if value else None


def _status(span: Span) -> dict[str, Any]:
    status = span["status"]
    error = status["message"] if status["code"] == "error" else None
    return {"status": status["code"], "error": error or None}


def _duration_ms(span: Span) -> float | None:
    if not span["end_time"]:
        return None
    return (parse_time(span["end_time"]) - parse_time(span["start_time"])).total_seconds() * 1000


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def text_sha(text: str) -> str:
    """SHA-256 hex digest of a text (output and prompt identities, embedding cache keys)."""
    return hashlib.sha256(text.encode()).hexdigest()
