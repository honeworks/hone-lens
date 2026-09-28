"""AC-9: the HTML report is one self-contained file; every finding links to views of its evidence; it opens
without network."""

import json
import re
from html.parser import HTMLParser

import pytest

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, synthetic_runs

pytestmark = pytest.mark.e2e


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.external: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        a = {k: v or "" for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag in ("script", "link", "img", "iframe") or (a.get("src") or "").startswith("http"):
            self.external.append(tag)


@pytest.fixture
def ws(tmp_path) -> tl.Workspace:
    runs = synthetic_runs(
        tmp_path, n_runs=300, plant=["homogeneity_from_example", "truncation", "vision_400"]
    )
    ws = tl.Workspace(
        tmp_path / "lens",
        embedder=FakeEmbedder.semantic(),
        llm=FakeTextClient.analyst(),
        replayer=FakeReplayer.removing_section_reduces_similarity(),
    )
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas")
    diversity = next(f for f in report.findings if f.category == "diversity")
    ws.test(diversity.id, variants=2, samples=10)
    return ws


def test_ac9_html_report_is_self_contained_and_links_every_finding_to_evidence(ws, tmp_path) -> None:
    path = tmp_path / "report.html"
    html = ws.report("song_ideas", fmt="html", path=path)
    assert path.read_text(encoding="utf-8") == html and html.startswith("<!doctype html>")
    page = _Page()
    page.feed(html)
    assert page.external == [] and "http://" not in html and "https://" not in html  # no network needed
    findings = ws.findings()
    assert findings and all(f.id in page.ids for f in findings)
    for f in findings:
        section = html[html.index(f'<section id="{f.id}">') :]
        section = section[: section.index("</section>")]
        targets = re.findall(r'href="#([^"]+)"', section)
        assert len(targets) >= min(len(f.evidence), 1)
        assert all(t in page.ids for t in targets), f.id  # every evidence link has its view in the file
    assert all(link[1:] in page.ids for link in page.links if link.startswith("#"))
    diversity = next(f for f in findings if f.category == "diversity")
    assert diversity.evidence[0] in page.ids  # the cluster view
    assert "confirmed" in html and "format_example" in html  # cause and replay test are shown
    trace_views = [i for i in page.ids if i.startswith("trace-")]
    assert len(trace_views) >= 20


def test_ac9_json_and_terminal_reports(ws) -> None:
    data = json.loads(ws.report("song_ideas", fmt="json"))
    assert data["workflow"] == "song_ideas"
    assert {f["id"] for f in data["findings"]} == {f.id for f in ws.findings()}
    assert all("affected_ids" not in f for f in data["findings"]) and data["clusters"]
    assert "members" not in data["clusters"][0]
    assert ws.report("song_ideas").startswith("hone-lens: song_ideas")
    with pytest.raises(tl.errors.HoneLensError, match="unknown report format 'pdf'"):
        ws.report(fmt="pdf")
