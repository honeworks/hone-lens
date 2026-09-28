"""Evidence that is not a stored span (e.g. `run/step/item` of a hone-flow step record) is plain text."""

from hone_lens.findings import Finding
from hone_lens.report import html
from hone_lens.testing.contracts import example_call_span


def test_html_renders_non_span_evidence_as_text() -> None:
    span = example_call_span()
    finding = Finding(
        "revisions", category="quality", id="F-0001", evidence=["run-7/lyrics/7", span["span_id"]]
    )
    page = html.render("w", [finding], {}, {span["trace_id"]: [span]})
    assert f'<a href="#trace-{span["trace_id"]}">{span["span_id"]}</a>' in page
    assert "run-7/lyrics/7" in page and 'href="#trace-"' not in page
    assert ">run-7/lyrics/7</a>" not in page
