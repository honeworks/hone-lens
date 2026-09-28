# Reports and the findings lifecycle

## Reports

`ws.report(workflow=None, *, fmt="terminal", path=None) -> str` renders the findings stored in the
workspace: every finding of `workflow` (all workflows when `None`) that is not dismissed, most important
first, including findings a later analysis no longer produced (so a fixed finding stays visible). The text
is returned, and written to `path` when one is given. It reads the workspace only; run `analyze` first.

```python
import json

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, synthetic_runs

runs = synthetic_runs("demo", n_runs=300, plant=["homogeneity_from_example", "truncation"])
ws = tl.Workspace("demo/lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
ws.ingest(f"hone:{runs.root}")
ws.analyze("song_ideas")

print(ws.report("song_ideas"))  # terminal (plain text)
data = json.loads(ws.report("song_ideas", fmt="json"))  # JSON
html = ws.report("song_ideas", fmt="html", path="demo/report.html")  # one HTML file
print(sorted(data), len(data["findings"]))  # ['clusters', 'findings', 'workflow'] 4
```

**terminal**: plain text with no dependencies: one block per finding with id, severity, category, title,
affected / total, scope, status, and the cause, fix, test result and first evidence ids when present.
`Report` objects from `analyze` render the same way and add the LLM cost and notes
(`hone_lens.report.terminal.render(report)`).

**json**: `{"workflow", "findings", "clusters"}`. Each finding has every field of `Finding` (see
[concepts.md](concepts.md#findings)); the full list of affected span ids is left out (it can be large;
`evidence` keeps a sample). `clusters` holds the clusters that findings cite as evidence, with their
`description` and `in_common` but without the member list.

**html**: one self-contained file, with inline CSS and no scripts or external assets, so it opens offline
and can be attached or archived. It has an index of the findings, one section per finding (scope, metric,
cause, fix, replay test, details), one per evidence cluster (description and sample outputs) and one per
evidence trace (the span tree with the key GenAI fields and outputs, the evidence spans highlighted).
Every evidence id in a finding links to its section: `#F-0001`, `#C-...`, `#trace-<trace id>`.

```python
f = ws.findings()[0]
assert f'<section id="{f.id}">' in html and 'href="#trace-' in html
```

## Lifecycle

hone-lens creates findings with status `new`. A person changes the status with
`ws.set_status(finding_id, status, note="")`; the note is kept in `details["note"]` across analyses.

| Status | Meaning | On re-analysis |
|---|---|---|
| `new` | found, not reviewed | stays `new` |
| `confirmed_by_human` | a person agrees it is real | stays |
| `dismissed` | not a problem, or accepted | stays dismissed; left out of reports and of `analyze` results |
| `fixed` | a fix was applied | re-checked: becomes `regressed` when found again in runs that started after the fix |
| `regressed` | came back after `fixed` | stays until a person changes it |

Findings are matched across analyses by their content key (detector, category, scope without time range,
metric name), so they keep their id, status, cause, fix and test result. Setting `fixed` records the
start time of the newest ingested span as `details["fixed_at"]`. If the same finding is produced again
only from runs up to that time, it stays `fixed`; if any affected run starts later, it becomes
`regressed`.

```python
truncation = next(f for f in ws.findings() if f.detector == "truncation")
ws.set_status(truncation.id, "fixed", note="raised max_tokens")
assert ws.finding(truncation.id).status == "fixed"

synthetic_runs("demo", n_runs=100, plant=["truncation"], seed=1)  # new, later runs: the issue is back
ws.ingest(f"hone:{runs.root}")
ws.analyze("song_ideas")
print(ws.finding(truncation.id).status)  # regressed

repair = next(f for f in ws.findings() if f.detector == "failure_rate")
ws.set_status(repair.id, "dismissed", note="repairs are expected for this model")
report = ws.analyze("song_ideas")
assert repair.id not in [f.id for f in report.findings]
assert [f.id for f in ws.findings(status="dismissed")] == [repair.id]
```

`ws.findings(status=None)` lists every stored finding, dismissed ones included, by id;
`ws.finding(id)` returns one or raises `HoneLensError` for an unknown id. In the CLI:
`hone-lens findings --status new`, `hone-lens set-status F-0002 fixed --note "..."` (see [cli.md](cli.md)).
