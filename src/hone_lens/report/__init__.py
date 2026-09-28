"""Reports of a workspace's findings: `terminal` (plain text), `json`, and `html` (one self-contained file
with a view of every evidence trace and cluster)."""

from __future__ import annotations

import json as jsonlib
from collections.abc import Sequence

from hone_lens.errors import HoneLensError
from hone_lens.findings import Finding, Report
from hone_lens.report import html, json, terminal
from hone_lens.store import AnalyticsDB

FORMATS = ("terminal", "json", "html")


def render(db: AnalyticsDB, workflow: str | None, findings: Sequence[Finding], fmt: str) -> str:
    """The findings of `workflow` as a `fmt` report."""
    if fmt == "terminal":
        return terminal.render(Report(workflow, list(findings)))
    if fmt not in FORMATS:
        raise HoneLensError(f"unknown report format {fmt!r}; choose from {list(FORMATS)}")
    cluster_ids = {e for f in findings for e in f.evidence if e.startswith("C-")}
    clusters = {c["id"]: c for c in db.query("SELECT * FROM clusters ORDER BY id") if c["id"] in cluster_ids}
    for c in clusters.values():
        c["members"] = jsonlib.loads(c["members"])
    if fmt == "json":
        return json.render(workflow, findings, list(clusters.values()))
    spans = [e for f in findings for e in f.evidence if not e.startswith("C-")]
    spans += [m for c in clusters.values() for m in c["members"][: html.CLUSTER_SAMPLES]]
    return html.render(workflow, findings, clusters, db.traces_of(spans))


__all__ = ["FORMATS", "render"]
