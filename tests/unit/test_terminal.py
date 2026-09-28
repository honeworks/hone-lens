import hone_lens as tl
from hone_lens.report.terminal import render


def test_empty_report() -> None:
    assert render(tl.Report(None, [])) == "hone-lens: all workflows\n========================\n0 findings\n"


def test_finding_lines_notes_and_cost() -> None:
    f = tl.Finding(
        "30% of w/s calls failed",
        "reliability",
        severity="high",
        scope={"workflow": "w", "step": "s", "model": "m", "time_range": ["a", "b"]},
        affected=30,
        total=100,
        evidence=[f"e{i}" for i in range(7)],
        id="F-0001",
    )
    other = tl.Finding("second", "cost", id="F-0002")
    report = tl.Report("w", [f, other], notes=["detector broken failed: boom"], cost_usd=0.5, llm_calls=3)
    text = render(report)
    assert "F-0001  [high] reliability  30% of w/s calls failed" in text
    assert "30/100 affected  step=s model=m  status=new" in text
    assert "evidence: e0, e1, e2, e3, e4 ..." in text
    assert text.index("F-0001") < text.index("F-0002")
    assert "LLM cost: $0.5000 in 3 calls\n" in text
    assert text.endswith("Notes:\n  - detector broken failed: boom\n")
    stopped = render(tl.Report("w", [], budget_exhausted=True))
    assert "(stopped at budget; results are partial)" in stopped
