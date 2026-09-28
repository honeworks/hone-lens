"""Stage 2: embed every output, cluster them, and measure how alike they are.

What: output analysis: outputs grouped into clusters of near-duplicates, homogeneity measures (largest
cluster share, distinct rate, mean pairwise cosine, repeated n-grams), the `homogeneity` finding when one
pattern dominates, and **closeness to prompt sections**: which recorded prompt section the dominant
cluster resembles.

How: give the workspace an `Embedder` (`Workspace(embedder=...)`) and run
`ws.analyze(workflow, stages=("outputs",))`. Every output text is embedded (cached per embedder model and
text hash), clustered per (workflow, step) with a deterministic built-in method, and compared across
prompt versions. Clusters are stored in the `clusters` table; the finding's `details` hold the numbers
and `details["closeness"]` the prompt sections, most over-represented first.

Why: a workflow can succeed on every call and still fail its purpose by saying the same thing again and
again. That only shows up across many outputs, and closeness to the prompt points at the likely cause.

Run: python examples/output_clusters.py
"""

import json
import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    # Prompt v3 adds a `format_example` section; 80% of v3 outputs copy its storm-and-captain pattern.
    runs = synthetic_runs(Path(tmp), n_runs=400, plant=["homogeneity_from_example"])
    embedder = FakeEmbedder.semantic()  # similar words -> similar vectors; any Embedder works here
    ws = tl.Workspace(Path(tmp) / "lens", embedder=embedder)
    ws.ingest(f"hone:{runs.root}")

    report = ws.analyze("song_ideas", stages=("outputs",))
    (f,) = report.findings
    print(f"{f.id} {f.detector}: {f.title}")
    print("largest-cluster share by prompt version:", f.details["by_prompt_version"])
    print("measures:", f.details["measures"])

    # Closeness: the cluster is much closer to the format_example section than the other outputs are.
    for c in f.details["closeness"][:2]:
        print(f"  section {c['section']} v{c['version']}: difference {c['difference']:.2f}")
    assert f.details["closeness"][0]["section"] == "format_example"

    # The clusters themselves, with their members (output span ids).
    for c in ws.db.query("SELECT id, size, total, members FROM clusters ORDER BY rank LIMIT 3"):
        sample = ws.db.output_texts(json.loads(c["members"])[:1])
        print(f"  {c['id']}: {c['size']}/{c['total']} outputs, e.g. {sample[0][:70]!r}")

    # Embeddings are cached: analyzing again embeds nothing new.
    calls = len(embedder.calls)
    ws.analyze("song_ideas", stages=("outputs",))
    assert len(embedder.calls) == calls
