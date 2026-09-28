"""Stage 3: let an LLM describe each output cluster, within a budget.

What: cluster descriptions (a one-sentence `description` and what the outputs have `in_common`), written by
the analysis LLM from a sample of each cluster, and the `Budget` that caps what such a stage may spend.

How: give the workspace a `TextClient` (`Workspace(llm=...)`) and run `ws.analyze(...)` with the
`"describe"` stage (on by default) and `budget=`: a USD amount, `"2usd"`, `"40 calls"`, `"5000 tokens"`, or
a `tl.Budget(usd=..., usd_per_1k_tokens=..., calls=..., tokens=...)`. At the limit the stage stops with
partial results; `Report.budget_exhausted`, `Report.notes`, `Report.cost_usd` and `Report.llm_calls` say
what happened. A description is stored with its cluster and not paid for again.

Why: numbers say that 40% of outputs look alike; a description says *how* ("storm opening, named captain,
three-line intro"), which is what a person needs to judge it. LLM calls cost money, so every LLM stage
takes a budget and reports its spend.

Run: python examples/describe_with_budget.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=300, plant=["homogeneity_from_example"])
    llm = FakeTextClient.analyst()  # answers hone-lens's analysis prompts; any TextClient works here
    ws = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic(), llm=llm)
    ws.ingest(f"hone:{runs.root}")

    # 1. A budget of zero calls: the stage stops before its first call and says so.
    stopped = ws.analyze("song_ideas", budget=tl.Budget(calls=0))
    print("budget exhausted:", stopped.budget_exhausted, "| notes:", stopped.notes)
    assert stopped.budget_exhausted and llm.calls == []

    # 2. A USD budget with a price per 1,000 tokens (local models cost nothing: use calls or tokens there).
    budget = tl.Budget(usd=0.05, usd_per_1k_tokens=0.002)
    report = ws.analyze("song_ideas", budget=budget)
    (f,) = report.findings
    print(f"\n{f.title}")
    print("description:", f.details["cluster_description"])
    print(f"spent ${report.cost_usd:.4f} in {report.llm_calls} call(s); wallet: {budget.spent_calls} call(s)")
    assert report.llm_calls == 1 and not report.budget_exhausted

    # 3. Described clusters keep their description: a re-analysis makes no LLM call.
    again = ws.analyze("song_ideas", budget="10 calls")
    assert again.llm_calls == 0 and again.findings[0].details["cluster_description"]
    print("re-analysis LLM calls:", again.llm_calls)
