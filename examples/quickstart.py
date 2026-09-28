"""The whole funnel on synthetic data, offline: ingest, analyze, explain, replay-test, HTML report.

What: the design's main example (design/current.md §3) end to end. 2,000 synthetic runs of a
`song_ideas` workflow with four planted issues; prompt v3 adds a `format_example` section that many
outputs copy. hone-lens finds the issues, traces the repetition to that section, confirms it with a replay
test and writes one HTML report.

How: `synthetic_runs(...)` writes the records; `tl.Workspace(path, embedder=, llm=, replayer=)` holds the
analysis; then `ws.ingest("hone:<folder>")`, `ws.analyze(workflow)` (stages 1-3), `ws.explain(id)`
(stage 5), `ws.test(id, variants=, samples=)` (stage 6) and `ws.report(workflow, fmt="html", path=)`.
The fakes from `hone_lens.testing` stand in for a real embedder, analysis LLM and replayer; swap in real
ones (examples/own_adapters.py, docs/adapters.md) and nothing else changes.

Why: start here. Every other example zooms into one of these calls.

Run: python examples/quickstart.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # 1. Recorded runs under root/runs: span stores (models, select) and hone-flow run folders.
    runs = synthetic_runs(
        root,
        n_runs=2000,
        plant=["homogeneity_from_example", "truncation", "judge_equals_generator", "score_zero_spike"],
    )

    # 2. A workspace with the three ports it needs for every stage.
    ws = tl.Workspace(
        root / "lens",
        embedder=FakeEmbedder.semantic(),  # similar words -> similar vectors
        llm=FakeTextClient.analyst(),  # answers hone-lens's analysis prompts plausibly
        replayer=FakeReplayer.removing_section_reduces_similarity(),  # without the example: varied ideas
    )
    ws.ingest(f"hone:{runs.root}")

    # 3. Stages 1-3: statistics, output clusters, cluster descriptions.
    report = ws.analyze("song_ideas")
    for f in report.findings:
        print(f"{f.id} [{f.severity}] {f.category}: {f.title}")

    # 4. Stage 5: which recorded input separates the affected runs from the others?
    diversity = next(f for f in report.findings if f.category == "diversity")
    explained = ws.explain(diversity.id)
    cause = explained.cause
    assert cause is not None and (cause.kind, cause.target) == ("prompt_section", "format_example")
    print(f"\ncause of {explained.id}: {cause.kind} {cause.target} (effect {cause.effect_size:.2f})")

    # 5. Stage 6: replay 20 affected calls with 2 variants of that section.
    t = ws.test(explained.id, variants=2, samples=20)
    assert t.confirmed and t.metric_after is not None and t.metric_before is not None
    assert t.metric_after < t.metric_before
    print(f"replay test: {t.metric_name} {t.metric_before} -> {t.metric_after} ({t.variant}); confirmed")

    # 6. One self-contained HTML file, every claim linked to its evidence.
    html = root / "report.html"
    ws.report("song_ideas", fmt="html", path=html)
    print(f"HTML report: {html.stat().st_size} bytes")
