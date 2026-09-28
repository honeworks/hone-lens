"""A custom detector: your own rule over the analytics tables, reported like the built-in ones.

What: a detector that flags song ideas longer than 150 characters, registered next to the built-in
detectors; its findings get ids, statuses, reports and causes like any other.

How: decorate a function `(db: tl.AnalyticsDB) -> list[tl.Finding]` with `@tl.detector(category=...)`.
Inside `ws.analyze(workflow)` the tables (`calls`, `steps`, `selections`, `outputs`, `run_calls`,
`flow_steps`; columns in docs/detectors.md) hold only that workflow's rows, so plain SQL through
`db.query(sql, params)` is enough. Build findings with `tl.finding(...)`, which counts the
affected rows, samples evidence span ids and keeps every affected id (which `explain` needs).

Why: every workflow has rules only its team knows (length limits, banned phrases, a step that must never
be skipped). A detector makes such a rule part of every analysis, with evidence.

Run: python examples/custom_detector.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import synthetic_runs

MAX_CHARS = 150


@tl.detector(category="quality")
def ideas_too_long(db: tl.AnalyticsDB) -> list[tl.Finding]:
    found: list[tl.Finding] = []
    # Inside analyze("song_ideas"), the tables only hold that workflow's rows.
    for scope in db.query("SELECT DISTINCT workflow, step FROM outputs"):
        params = {**scope, "max_chars": MAX_CHARS}
        in_scope = "FROM outputs WHERE workflow = :workflow AND step = :step"
        total = db.query("SELECT count(*) AS n " + in_scope, params)[0]["n"]
        rows = db.query("SELECT span_id, start_time " + in_scope + " AND length(text) > :max_chars", params)
        if len(rows) >= 3:
            # tl.finding() counts the rows, samples evidence, records the time range and keeps every affected
            # span id (which explain() needs).
            found.append(
                tl.finding(
                    f"{len(rows)} of {total} ideas are longer than {MAX_CHARS} characters",
                    category="quality",
                    scope=scope,
                    affected=rows,
                    total=total,
                    severity="low",
                    metric={"name": "long_idea_rate", "value": len(rows) / total, "baseline": 0.0},
                )
            )
    return found


with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=300)
    ws = tl.Workspace(Path(tmp) / "lens")
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats",))  # no model needed
    mine = [f for f in report.findings if f.detector == "ideas_too_long"]
    assert mine, "the custom detector found nothing"
    print(ws.report("song_ideas"))

    # Causes work for custom findings too: which recorded input goes with long ideas? In this data the
    # answer is "none": long ideas are spread evenly over prompt versions, models and parameters.
    explained = ws.explain(mine[0].id)
    assert explained.cause is not None and explained.cause.kind == "none"
    print(f"cause: {explained.cause.kind} {explained.cause.target!r} ({explained.cause.hypothesis})")
