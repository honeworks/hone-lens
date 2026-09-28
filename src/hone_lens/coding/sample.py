"""A stratified sample of a workflow's outputs for review: every cluster, every finding's evidence and every
score range gets its turn."""

from __future__ import annotations

import json
import random

from hone_lens.stats import Row
from hone_lens.store import AnalyticsDB

SEED = 0
SCORE_BUCKETS = ((0.34, "low"), (0.67, "mid"), (float("inf"), "high"))


def stratified_sample(db: AnalyticsDB, workflow: str, n: int) -> list[Row]:
    """Up to `n` outputs (`span_id`, `text`), taken round-robin from the strata (clusters, the rest,
    detector evidence, score ranges) in a seeded random order."""
    outputs = db.query(
        "SELECT span_id, run_id, text FROM outputs WHERE workflow IS ? ORDER BY start_time, span_id",
        (workflow,),
    )
    by_id = {r["span_id"]: r for r in outputs}
    rng = random.Random(SEED)
    queues = [rng.sample(ids, len(ids)) for ids in _strata(db, workflow, outputs).values() if ids]
    picked: dict[str, Row] = {}
    while len(picked) < min(n, len(outputs)) and any(queues):
        for queue in queues:
            while queue and queue[0] in picked:
                queue.pop(0)
            if queue and len(picked) < n:
                span_id = queue.pop(0)
                picked[span_id] = by_id[span_id]
    return list(picked.values())


def _strata(db: AnalyticsDB, workflow: str, outputs: list[Row]) -> dict[str, list[str]]:
    known = {r["span_id"] for r in outputs}
    strata: dict[str, list[str]] = {}
    for c in db.query("SELECT id, members FROM clusters WHERE workflow IS ? ORDER BY rank, id", (workflow,)):
        strata[f"cluster:{c['id']}"] = [s for s in json.loads(c["members"]) if s in known]
    clustered = {s for ids in strata.values() for s in ids}
    strata["cluster:none"] = [r["span_id"] for r in outputs if r["span_id"] not in clustered]
    for f in db.findings():
        if f.scope.get("workflow") == workflow and f.status != "dismissed":
            strata[f"finding:{f.id}"] = [s for s in f.evidence if s in known]
    scores = db.query(
        "SELECT run_id, min(value) AS value FROM selections WHERE kind = 'score' AND value IS NOT NULL "
        "AND workflow IS ? GROUP BY run_id",
        (workflow,),
    )
    bucket = {r["run_id"]: next(name for top, name in SCORE_BUCKETS if r["value"] < top) for r in scores}
    for r in outputs:
        if r["run_id"] in bucket:
            strata.setdefault(f"score:{bucket[r['run_id']]}", []).append(r["span_id"])
    return strata
