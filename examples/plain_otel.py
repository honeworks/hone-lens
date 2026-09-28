"""Plain OpenTelemetry GenAI traces: no honeworks packages, no `hone.*` attributes.

What: analysis of traces recorded by any OpenTelemetry GenAI instrumentation, read from OTLP/JSON files
(e.g. an OpenTelemetry Collector file exporter): what works without `hone.*` attributes and what the
finding says when a deeper cause is unavailable.

How: `ws.ingest(OtlpJsonFiles("<glob>"))` (or the string `"otlp:<glob>"`). The workflow is the resource
`service.name`, the step is the parent span's name and a run is a trace; older `gen_ai.*` names are
mapped on read. Statistics and output findings work as usual; `explain` finds no prompt-section cause
(that needs `hone.models.prompt.sections`) and says so in `details["notes"]`.

Why: hone-lens is useful without the rest of honeworks. Standard traces get the same funnel, and the
notes say which records would make the analysis deeper.

Run: python examples/plain_otel.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.sources import OtlpJsonFiles
from hone_lens.testing import FakeEmbedder, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=400, plant=["homogeneity_from_example", "truncation"])
    print(f"OTLP file: {runs.otlp_path.name}")  # older gen_ai.* names, mapped on read

    ws = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic())
    (ingested,) = ws.ingest(OtlpJsonFiles(str(runs.otlp_path.parent / "*.json")))
    print(f"ingested {ingested.added} spans")

    report = ws.analyze("song_ideas")  # no llm: the describe stage is skipped, with a note
    for f in report.findings:
        print(f"{f.id} {f.detector}: {f.title}")
    print("notes:", report.notes)
    assert {"truncation", "homogeneity"} <= {f.detector for f in report.findings}  # stats and outputs work

    diversity = next(f for f in report.findings if f.category == "diversity")
    explained = ws.explain(diversity.id)
    assert explained.cause is not None and explained.cause.kind != "prompt_section"
    print(f"cause: {explained.cause.kind} {explained.cause.target!r}")
    print("notes:", explained.details["notes"])
    assert any("section" in note for note in explained.details["notes"])  # says what is missing
