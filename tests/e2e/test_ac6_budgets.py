"""AC-6: LLM stages stop at the budget with partial results and a cost report.

Stage 3 (cluster descriptions) and `label_all` (estimate first, then confirmation, then budget).
"""

import json

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeRecordSource, FakeTextClient, ScriptedIO

pytestmark = pytest.mark.e2e

PATTERNS = {  # three recurring output patterns (40 / 30 / 20 outputs) and 30 varied ones
    "A storm opens the song as Captain {} steers through black water.": 40,
    "Grandma {} bakes bread in the quiet kitchen at dawn every morning.": 30,
    "Robot {} dances alone under neon rain on the empty rooftop tonight.": 20,
}
VARIED = [
    f"{a} {b} {c}"
    for a, b, c in zip(
        ["red", "blue", "green", "gold", "pink", "grey", "teal", "navy", "rust", "sand"],
        ["fox", "owl", "cat", "eel", "ant", "bee", "yak", "elk", "emu", "gnu"],
        ["sings", "runs", "hides", "waits", "falls", "rises", "spins", "sleeps", "jumps", "reads"],
        strict=True,
    )
]


def _spans() -> list[dict]:
    texts = [p.format(i) for p, n in PATTERNS.items() for i in range(n)]
    texts += [f"{v} in {w}" for v in VARIED for w in ("spring", "summer", "winter")]
    return [
        {
            "trace_id": f"{i:032x}",
            "span_id": f"{i:016x}",
            "name": "chat m",
            "start_time": f"2026-09-01T00:{i // 60:02d}:{i % 60:02d}.000Z",
            "resource": {"service.name": "ideas"},
            "attributes": {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "m",
                "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": t}]),
            },
        }
        for i, t in enumerate(texts)
    ]


def test_ac6_describe_stops_at_budget_with_partial_results_and_cost(tmp_path) -> None:
    llm = FakeTextClient.analyst()
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=llm)
    ws.ingest(FakeRecordSource(_spans()))

    report = ws.analyze("ideas", budget="2 calls")
    assert report.llm_calls == 2 and len(llm.calls) == 2 and report.budget_exhausted
    assert report.cost_usd == 0.0  # the fake reports tokens but no price
    assert any(n.startswith("describe stopped: call budget of 2 spent") for n in report.notes)
    described = ws.db.query("SELECT size FROM clusters WHERE description IS NOT NULL ORDER BY size DESC")
    assert [c["size"] for c in described] == [40, 30]  # the largest clusters first
    (largest,) = [f for f in report.findings if f.metric["name"] == "largest_cluster_share"]
    assert "storm" in largest.details["in_common"] and "storm" in largest.title
    text = tl.Workspace(tmp_path / "lens").report("ideas")
    assert "storm" in text

    again = ws.analyze("ideas", budget=tl.Budget(usd=1.0, usd_per_1k_tokens=0.5))
    assert again.llm_calls == 1 and not again.budget_exhausted  # only the undescribed cluster costs again
    assert 0 < again.cost_usd < 1.0
    assert ws.db.query("SELECT count(*) AS n FROM clusters WHERE description IS NULL")[0]["n"] == 0


def test_ac6_label_all_shows_an_estimate_and_needs_confirmation(tmp_path) -> None:
    llm = FakeTextClient.analyst()
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic(), llm=llm)
    ws.ingest(FakeRecordSource(_spans()))
    ws.analyze("ideas", stages=("outputs",))
    ws.review("ideas", io=ScriptedIO(), sample=8)  # accept everything the AI proposes
    calls_after_review = len(llm.calls)

    io = ScriptedIO(["n"])
    declined = ws.label_all("ideas", budget=tl.Budget(usd=1.0, usd_per_1k_tokens=0.01), io=io)
    assert declined.refused.startswith("not confirmed") and declined.labeled == 0
    assert io.shown[0].startswith("label_all: 120 outputs of ideas, one LLM call each, about ")
    assert (
        declined.estimate_tokens > 120 * 400 and declined.estimate_usd == declined.estimate_tokens / 100_000
    )
    assert len(llm.calls) == calls_after_review  # nothing was spent before the answer

    silent = ws.label_all("ideas", budget=None)  # no io and no yes: without a terminal nobody confirms
    assert silent.refused.startswith("not confirmed") and len(llm.calls) == calls_after_review

    partial = ws.label_all("ideas", budget="20 calls", yes=True)
    assert partial.budget_exhausted and partial.llm_calls == 20
    assert partial.labeled == 8 + 12  # the reviewed sample plus what the rest of the budget allowed
    assert sum(partial.counts.values()) == partial.labeled
    assert any(n.startswith("label_all stopped: call budget of 20 spent") for n in partial.notes)
