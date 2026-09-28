"""Stage 2: output analysis. Embed every output, cluster per (workflow, step), measure homogeneity, and
compare the largest cluster with the prompt sections to find what the outputs copy."""

from __future__ import annotations

import hashlib
from typing import Any

from hone_lens.findings import Finding
from hone_lens.outputs.closeness import NO_SECTIONS, closeness, section_texts
from hone_lens.outputs.cluster import Clustering, cluster
from hone_lens.outputs.embed import embed
from hone_lens.outputs.homogeneity import homogeneity, measures, outliers
from hone_lens.ports import Embedder
from hone_lens.stats import Row, group_by
from hone_lens.store import AnalyticsDB

MIN_OUTPUTS = 20
SEED = 0


def analyze_outputs(db: AnalyticsDB, embedder: Embedder) -> list[Finding]:
    """Stage 2 findings for every (workflow, step) with enough outputs; the clusters are saved in the
    workspace (`clusters` table) for descriptions and reports."""
    rows = db.query(
        "SELECT span_id, start_time, workflow, step, template_version, text, text_sha FROM outputs "
        "ORDER BY start_time, span_id"
    )
    findings: list[Finding] = []
    for (workflow, step), group in group_by(rows, "workflow", "step").items():
        if len(group) < MIN_OUTPUTS:
            continue
        scope = {"workflow": workflow, "step": step}
        vectors = embed(db, embedder, [r["text"] for r in group])
        c = cluster(vectors, SEED)
        ids = _save_clusters(db, scope, group, c)
        details: dict[str, Any] = {"cluster_id": ids[0] if ids else None}
        if ids:
            sections = section_texts(db, [r["span_id"] for r in group])
            details["closeness"] = closeness(db, embedder, sections, vectors, c.labels == 0)
            details["notes"] = [] if sections else [NO_SECTIONS]
        findings += homogeneity(scope, group, measures(group, vectors, c), c, details)
        findings += outliers(scope, group, c)
    return findings


def _save_clusters(db: AnalyticsDB, scope: dict[str, Any], rows: list[Row], c: Clustering) -> list[str]:
    clusters: list[dict[str, Any]] = []
    for k, center in enumerate(c.centers):
        members = [r["span_id"] for r, label in zip(rows, c.labels, strict=True) if label == k]
        key = f"{scope['workflow']}/{scope['step']}/{rows[center]['span_id']}"
        clusters.append(
            {
                "id": "C-" + hashlib.sha256(key.encode()).hexdigest()[:8],
                "workflow": scope["workflow"],
                "step": scope["step"],
                "rank": k,
                "size": len(members),
                "total": len(rows),
                "center": rows[center]["span_id"],
                "members": members,
            }
        )
    db.save_clusters(scope["workflow"], scope["step"], clusters)
    return [cl["id"] for cl in clusters]
