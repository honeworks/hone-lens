"""Stage 5: find the recorded input that separates the affected runs from the others.

What: cause ranking for a finding: every recorded input of the runs in the finding's scope (prompt
version, each prompt section and its version, model, temperature, step parameters, step code version) is
compared between affected and unaffected runs; the strongest one becomes the suspected `cause`, with an
effect size, a 95% confidence interval, a hypothesis and a proposed `fix`. When no input separates the
runs, the cause is `none` and the finding says so.

How: `ws.explain(finding_id)` returns the updated `Finding`: `cause` (`kind`, `target`, `effect_size`,
`ci`, `status="suspected"`, `hypothesis`), `fix` (`kind`, `description`, `diff`) and
`details["causes"]`, the full ranking. With `Workspace(llm=...)` the hypothesis is written by the LLM from
the top causes and sample outputs; without one it is built from the numbers.

Why: "40% of outputs look alike" is only actionable with "... since prompt v3 added the format example".
Effect sizes with intervals keep the claim honest; the cause stays *suspected* until a replay test
(examples/replay_test.py) or a person confirms it.

Run: python examples/explain_cause.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=400, plant=["homogeneity_from_example", "truncation"])
    ws = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", stages=("stats", "outputs"))

    # 1. A finding with a cause: the homogeneity comes with the format_example section of prompt v3.
    diversity = next(finding for finding in report.findings if finding.category == "diversity")
    homogeneity = ws.explain(diversity.id)
    cause, fix = homogeneity.cause, homogeneity.fix
    assert cause is not None and fix is not None and cause.ci is not None and cause.effect_size is not None
    print(f"{homogeneity.id}: cause {cause.kind} {cause.target!r}, effect {cause.effect_size:.2f}")
    print(f"  95% confidence interval: {cause.ci[0]:.2f} .. {cause.ci[1]:.2f}")
    print("  status:", cause.status)
    print("  hypothesis:", cause.hypothesis)
    print("  fix:", fix.kind, "|", fix.description)
    ranking = homogeneity.details["causes"][:3]  # every compared input, strongest first
    print("  ranking:", [(c["kind"], c["target"], round(c["effect"], 2)) for c in ranking])
    assert (cause.kind, cause.target, cause.status) == ("prompt_section", "format_example", "suspected")

    # 2. A finding no recorded input explains: truncation was planted at random, in every prompt version.
    cut_off = next(finding for finding in report.findings if finding.metric["name"] == "truncation_rate")
    truncation = ws.explain(cut_off.id)
    assert truncation.cause is not None
    print(f"\n{truncation.id}: cause {truncation.cause.kind!r}; notes: {truncation.details['notes']}")
    assert truncation.cause.kind == "none"
