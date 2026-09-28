"""A single, self-contained HTML report (no scripts, no external assets): findings, and inside the same
file a view of every evidence trace and cluster, linked from each finding."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from hone_lens.findings import Finding
from hone_lens.mapping import messages_text

Span = Mapping[str, Any]
CLUSTER_SAMPLES = 10  # sample outputs shown per cluster
_STYLE = """
body{font:14px/1.45 system-ui,sans-serif;max-width:1100px;margin:2em auto;padding:0 1em;color:#222}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}
.high{color:#b00020}.medium{color:#b36b00}.low{color:#555}section{border-top:2px solid #333;margin-top:2em}
pre{white-space:pre-wrap;background:#f6f6f6;padding:6px;max-height:20em;overflow:auto}.evidence{background:#fff6d6}
"""
_KEYS = (
    "gen_ai.request.model",
    "hone.models.prompt.template_version",
    "gen_ai.response.finish_reasons",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "hone.models.structured.path",
    "hone.select.score.value",
    "hone.flow.status",
    "hone.flow.attempt",
    "hone.flow.reused_from",
    "hone.flow.gate.decision",
)


def render(
    workflow: str | None,
    findings: Sequence[Finding],
    clusters: Mapping[str, dict[str, Any]],
    traces: Mapping[str, Sequence[Span]],
) -> str:
    """The report page. `clusters`: id -> cluster row (with `members`); `traces`: trace id -> its spans."""
    trace_of = {s["span_id"]: trace_id for trace_id, spans in traces.items() for s in spans}
    evidence = {e for f in findings for e in f.evidence}
    body = [f'<h1 id="top">hone-lens report: {escape(workflow or "all workflows")}</h1>', _index(findings)]
    body += [_finding(f, trace_of) for f in findings]
    body += [_cluster(cid, c, trace_of) for cid, c in clusters.items()]
    body += [_trace(trace_id, spans, evidence) for trace_id, spans in traces.items()]
    title = escape(f"hone-lens: {workflow or 'all workflows'}")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{_STYLE}</style></head><body>{''.join(body)}</body></html>\n"
    )


def _index(findings: Sequence[Finding]) -> str:
    rows = "".join(
        f'<tr><td><a href="#{f.id}">{f.id}</a></td><td class="{f.severity}">{f.severity}</td>'
        f"<td>{escape(f.category)}</td><td>{escape(f.title)}</td><td>{f.affected}/{f.total}</td>"
        f"<td>{escape(f.status)}</td><td>{escape(_cause_text(f))}</td></tr>"
        for f in findings
    )
    head = "".join(
        f"<th>{h}</th>" for h in ("id", "severity", "category", "finding", "affected", "status", "cause")
    )
    head = f"<tr>{head}</tr>"
    return f"<p>{len(findings)} findings, most important first.</p><table>{head}{rows}</table>"


def _cause_text(f: Finding) -> str:
    if f.cause is None:
        return "not explained yet"
    return f"{f.cause.kind} {f.cause.target} ({f.cause.status})".strip()


def _finding(f: Finding, trace_of: Mapping[str, str]) -> str:
    links: list[str] = []
    for e in f.evidence:
        if e.startswith("C-"):
            target = e
        elif e in trace_of:
            target = f"trace-{trace_of[e]}"
        else:  # not a stored span (e.g. `run/step/item` of a hone-flow step record): plain text
            links.append(escape(e))
            continue
        links.append(f'<a href="#{escape(target)}">{escape(e)}</a>')
    parts = [
        f'<section id="{f.id}"><h2>{f.id}: {escape(f.title)}</h2>',
        f"<p class='{f.severity}'>{f.severity} · {escape(f.category)} · {f.affected}/{f.total} affected · "
        f"status {escape(f.status)} · detector {escape(f.detector)}</p>",
        _pairs("scope", f.scope),
        _pairs("metric", f.metric),
    ]
    if f.cause:
        parts.append(_pairs("cause", vars(f.cause)))
    if f.fix:
        parts.append(_pairs("fix", {"kind": f.fix.kind, "description": f.fix.description}))
        if f.fix.diff:
            parts.append(f"<pre>{escape(f.fix.diff)}</pre>")
    if f.test:
        test: dict[str, Any] = {k: v for k, v in vars(f.test).items() if k != "variants"}
        parts.append(_pairs("replay test", test))
    details = {k: v for k, v in f.details.items() if k not in ("measures",)}
    parts.append(_pairs("details", details))
    parts.append(f"<p>Evidence: {', '.join(links)}</p><p><a href='#top'>back to the list</a></p></section>")
    return "".join(parts)


def _pairs(title: str, values: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><th>{escape(str(k))}</th><td>{escape(_text(v))}</td></tr>" for k, v in values.items()
    )
    return f"<h3>{escape(title)}</h3><table>{rows}</table>"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _cluster(cluster_id: str, c: Mapping[str, Any], trace_of: Mapping[str, str]) -> str:
    samples = "".join(
        f'<li><a href="#trace-{escape(trace_of.get(s, ""))}">{escape(s)}</a></li>'
        for s in c["members"][:CLUSTER_SAMPLES]
        if s in trace_of
    )
    return (
        f'<section id="{escape(cluster_id)}"><h2>Cluster {escape(cluster_id)}</h2>'
        f"<p>{c['size']} of {c['total']} outputs of {escape(str(c['workflow']))}/{escape(str(c['step']))}</p>"
        f"<p>{escape(c.get('description') or 'not described')}</p>"
        f"<p>In common: {escape(c.get('in_common') or '-')}</p><ul>{samples}</ul></section>"
    )


def _trace(trace_id: str, spans: Sequence[Span], evidence: set[str]) -> str:
    depth = _depths(spans)
    rows: list[str] = []
    for s in sorted(spans, key=lambda s: (s["start_time"], s["span_id"])):
        a: Mapping[str, Any] = s["attributes"]
        facts = "; ".join(f"{k.split('.')[-1]}={_text(a[k])}" for k in _KEYS if k in a)
        output = messages_text(a.get("gen_ai.output.messages"))
        mark = ' class="evidence"' if s["span_id"] in evidence else ""
        indent = "&nbsp;" * 4 * depth[s["span_id"]]
        status = f"{s['status']['code']} {s['status']['message']}"
        text = f"<pre>{escape(output)}</pre>" if output else ""
        rows.append(
            f'<tr id="span-{escape(s["span_id"])}"{mark}><td>{indent}{escape(s["name"])}</td>'
            f"<td>{escape(s['start_time'])}</td><td>{escape(status)}</td><td>{escape(facts)}{text}</td></tr>"
        )
    tid = escape(trace_id)
    return f'<section id="trace-{tid}"><h2>Trace {tid}</h2><table>{"".join(rows)}</table></section>'


def _depths(spans: Sequence[Span]) -> dict[str, int]:
    parent = {s["span_id"]: s.get("parent_span_id") for s in spans}

    def depth(span_id: str) -> int:
        d, seen = 0, {span_id}
        while parent.get(span_id) in parent and parent[span_id] not in seen:
            span_id = str(parent[span_id])
            seen.add(span_id)
            d += 1
        return d

    return {s: depth(s) for s in parent}
