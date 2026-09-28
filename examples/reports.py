"""Reports: the same findings as terminal text, JSON and one self-contained HTML file.

What: the three report formats. Terminal: plain text for people at a prompt. JSON: every finding field
plus the clusters cited as evidence, for tools and CI. HTML: one file (inline CSS, no scripts, no network)
where every finding links to views of its evidence traces and clusters.

How: `ws.report(workflow, fmt="terminal" | "json" | "html", path=None)` renders the findings stored in
the workspace (dismissed ones left out) and returns the text, writing it to `path` when given. It only
reads the workspace, so run `ws.analyze(...)` first. A `Report` returned by `analyze` also renders as
text with `hone_lens.report.terminal.render(report)`, including cost and notes.

Why: findings are only useful when someone reads and checks them. The HTML file travels well (attach it
to a ticket, archive it next to a release) and lets a reader click from any claim to the spans behind it.

Run: python examples/reports.py
"""

import json
import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=300, plant=["homogeneity_from_example", "truncation"])
    ws = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas")

    # Terminal: plain text.
    text = ws.report("song_ideas")
    print(text.splitlines()[0])

    # JSON: {"workflow", "findings", "clusters"}; each finding with every Finding field.
    data = json.loads(ws.report("song_ideas", fmt="json"))
    print(sorted(data), [f["id"] for f in data["findings"]])
    first = data["findings"][0]
    print("first finding fields:", sorted(first)[:8], "...")
    assert {"id", "title", "scope", "metric", "evidence", "status"} <= set(first)

    # HTML: one file; every evidence id is a link to a section of the same page.
    path = Path(tmp) / "report.html"
    html = ws.report("song_ideas", fmt="html", path=path)
    print(f"HTML: {path.stat().st_size} bytes, {html.count('<section')} sections")
    assert "<script" not in html and "http://" not in html and "https://" not in html
    for f in data["findings"]:
        assert f'<section id="{f["id"]}">' in html
    assert 'href="#trace-' in html and 'href="#C-' in html
