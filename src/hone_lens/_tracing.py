"""Trace context (current.md §6). hone-lens receives no trace from its callers' records; it starts one trace
per operation that calls another package about a finding (explain's hypothesis, replay tests) and passes
it to every call, with `hone.lens.finding_id` (current.md §7)."""

from __future__ import annotations

import secrets
from collections.abc import Mapping


def trace_for_finding(finding_id: str, trace: Mapping[str, str] | None = None) -> dict[str, str]:
    """`trace` (or a new trace when it has no `traceparent`) plus `hone.lens.finding_id`."""
    context = dict(trace or {})
    context.setdefault("traceparent", f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01")
    context["hone.lens.finding_id"] = finding_id
    return context
