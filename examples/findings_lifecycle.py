"""The findings lifecycle: confirm, dismiss, mark fixed, and let re-analysis re-check.

What: finding statuses `new`, `confirmed_by_human`, `dismissed`, `fixed` and `regressed`, and what
re-analysis does with each: findings keep their id (matched by a content key), dismissed ones stay
dismissed and out of reports, fixed ones become `regressed` when they show up in runs after the fix.

How: `ws.set_status(finding_id, status, note="")` records a person's decision (the note is kept in
`details["note"]`); `ws.findings(status=...)` lists stored findings; every `ws.analyze(...)` matches what
it finds against the stored findings.

Why: analysis runs again and again as new runs arrive. Without memory, every run would re-report the same
issues, a dismissed false alarm would come back, and a fix nobody re-checked could quietly break again.

Run: python examples/findings_lifecycle.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import synthetic_runs


def id_of(report: tl.Report, metric: str) -> str:
    """The id of the report's finding with this metric (one each in these runs)."""
    (found,) = [f.id for f in report.findings if f.metric["name"] == metric]
    return found


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    runs = synthetic_runs(root, n_runs=300, plant=["truncation", "vision_400"])
    ws = tl.Workspace(root / "lens")
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats",))
    print("found:", [(f.id, f.detector, f.metric["name"]) for f in report.findings])
    first = {m: id_of(report, m) for m in ("error_rate", "capability_error_rate", "truncation_rate")}

    # A person reviews the findings.
    ws.set_status(first["error_rate"], "confirmed_by_human", note="cover-art calls fail: wrong model")
    ws.set_status(first["capability_error_rate"], "dismissed", note="cover-art scoring is being removed")
    ws.set_status(first["truncation_rate"], "fixed", note="raised max_tokens to 800")

    # Re-analysis of the same runs: same ids, statuses kept; the dismissed one is not reported.
    again = ws.analyze("song_ideas", stages=("stats",))
    assert first["capability_error_rate"] not in [f.id for f in again.findings]
    assert id_of(again, "truncation_rate") == first["truncation_rate"]
    # found again, but only in runs from before the fix: still fixed
    assert ws.finding(first["truncation_rate"]).status == "fixed"
    print("statuses:", {f.id: f.status for f in ws.findings()})

    # New runs without truncation: the fixed finding stays fixed.
    synthetic_runs(root, n_runs=100, plant=["vision_400"], seed=1)
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas", stages=("stats",))
    assert ws.finding(first["truncation_rate"]).status == "fixed"

    # Newer runs cut off again: the fixed finding is now `regressed`, with the note still attached.
    synthetic_runs(root, n_runs=100, plant=["truncation"], seed=2)
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas", stages=("stats",))
    back = ws.finding(first["truncation_rate"])
    print(f"{back.id}: {back.status} (note: {back.details['note']!r})")
    assert back.status == "regressed"
    assert [f.id for f in ws.findings(status="dismissed")] == [first["capability_error_rate"]]
    assert ws.finding(first["error_rate"]).status == "confirmed_by_human"
