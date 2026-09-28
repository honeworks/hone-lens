import re

from hone_lens._tracing import trace_for_finding


def test_trace_for_finding_starts_a_new_trace_with_the_finding_id() -> None:
    a, b = trace_for_finding("F-0001"), trace_for_finding("F-0001")
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", a["traceparent"])
    assert a["hone.lens.finding_id"] == "F-0001"
    assert a["traceparent"] != b["traceparent"]
