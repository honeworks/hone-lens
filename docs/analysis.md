# Analysis: outputs, descriptions, review, causes, replay tests

This page covers stages 2 to 6 (stage 1 is in [detectors.md](detectors.md)). The examples share one
workspace with 200 synthetic runs of the `song_ideas` workflow: prompt v2 for the first half, v3 for the
second half, and v3 adds a `format_example` section that 80% of its outputs copy. The fakes from
`hone_lens.testing` stand in for real models; with real ones the calls are the same.

```python
import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, ScriptedIO, synthetic_runs

runs = synthetic_runs("demo", n_runs=200, plant=["homogeneity_from_example"])
ws = tl.Workspace(
    "demo/lens",
    embedder=FakeEmbedder.semantic(),
    llm=FakeTextClient.analyst(),
    replayer=FakeReplayer.removing_section_reduces_similarity(),
)
ws.ingest(f"hone:{runs.root}")
```

## `analyze`

```python
report = ws.analyze("song_ideas")
print(report.findings[0].title)
# 40% of song_ideas/ideas outputs share one pattern (black, captain, intro, lantern, ...)
print(report.notes, report.cost_usd, report.llm_calls, report.budget_exhausted)
# [] 0.0 1 False
```

`ws.analyze(workflow=None, *, since=None, stages=("stats", "outputs", "describe"), budget=None)`:

- `workflow`: analyze one workflow (`None`: all of them).
- `since`: only runs that start at or after this time: an ISO-8601 string, a `datetime`, or a duration back
  from now such as `"30d"`, `"12h"`, `"45m"`.
- `stages`: any subset of `"stats"`, `"outputs"`, `"describe"`.
- `budget`: limits the describe stage (see [concepts.md](concepts.md#budgets)).

It returns a `Report`: `findings` (most important first, dismissed ones left out), `notes` (skipped
stages, failed detectors, model errors, budget stops), `cost_usd`, `llm_calls` and `budget_exhausted`.
Every finding is also stored in the workspace (`ws.findings()`, `ws.finding(id)`).

```python
recent = ws.analyze("song_ideas", since="2026-08-01T00:50:00Z", stages=("stats", "outputs"))
print(len(recent.findings), recent.notes)
```

## Stage 2: outputs

With an `embedder`, every output text is embedded (vectors are cached in the workspace per embedder model
and text hash, so a re-analysis embeds only new texts) and clustered per (workflow, step). Steps with
fewer than 20 outputs are skipped. Clustering is built in and deterministic: a seeded sample of at most
2,000 outputs picks cluster centres (outputs with the most neighbours at cosine similarity >= 0.8;
a cluster needs at least 5 members and 2% of the outputs), then every output joins its nearest centre when
close enough. Everything else belongs to no cluster.

A `homogeneity` finding comes out when the largest cluster holds >= 20% of the step's outputs or rose >=
10 points from the first prompt version to the last, or when fewer than 50% of the outputs are distinct.
Its `details` hold the numbers:

```python
f = report.findings[0]
print(f.details["cluster_id"], f.details["by_prompt_version"])  # C-..., {'2': 0.0, '3': 0.79}
print(sorted(f.details["measures"]))
# ['clusters', 'distinct_rate', 'largest_cluster_share', 'mean_pairwise_cosine', 'outputs',
#  'repeated_ngram_rate']
top = f.details["closeness"][0]
print(top["section"], round(top["difference"], 2))  # format_example 0.84
```

**Closeness to prompt sections.** When calls record their prompt sections
(`hone.models.prompt.sections`), each section text is embedded and compared with the largest cluster and
with the other outputs. `details["closeness"]` lists the sections, most over-represented first: here the
cluster is much closer to `format_example` than the other outputs are. Without recorded sections,
`details["notes"]` says that a section-level cause is unavailable.

The `outliers` detector flags outputs far from everything else.

## Stage 3: descriptions

With an `llm`, each cluster without a description (largest first) is described in one call: an evenly
spread sample of 20 of its outputs goes to the LLM, which returns a one-sentence `description` and what
the outputs have `in_common`. Descriptions are stored with the cluster and not paid for again while the
cluster keeps its id. They show up in the finding's title and in `details`:

```python
print(f.details["cluster_description"])
```

## Stage 4: review and labeling

`ws.review(workflow, *, io=None, sample=40, budget=None) -> Taxonomy` is a session between a person and
the AI:

1. A stratified sample of outputs: each cluster, the unclustered outputs, each finding's evidence and
   three score ranges, round-robin in a seeded order.
2. For each sampled output the AI writes a one-line note; the person presses Enter to accept it or types a
   better one.
3. The AI proposes 5 to 8 failure modes from the notes. For each, the person presses Enter to keep it,
   types a new name to rename it, `-` to drop it, or `=other_name` to merge it into another mode. Then
   the person adds modes as `name: description` until an empty answer.
4. For each sampled output the AI suggests a mode; the person accepts (Enter), types a mode name, or `-`
   for none.

The confirmed `Taxonomy` (`modes`, and `notes` with the person's labels) is stored in the workspace and
returned. `io` is anything with `show(text)` and `ask(question, default="") -> str`. The default,
`tl.ConsoleIO()`, uses the terminal; without a terminal `review` refuses to start (a taxonomy nobody
confirmed would make the agreement check below meaningless). In tests and scripts, use
`hone_lens.testing.ScriptedIO(answers)`: it answers from the list, then with each question's default
(accepting the AI's suggestion), and records what was shown and asked.

```python
answers = [""] * 10  # accept the AI's ten notes
answers += ["copies_example"]  # rename the one proposed mode
answers += ["vague: the idea lacks concrete details", ""]  # add a mode, then done
io = ScriptedIO(answers)  # the ten labels get the AI's suggestions
taxonomy = ws.review("song_ideas", io=io, sample=10)
print([m.name for m in taxonomy.modes])  # ['copies_example', 'vague']
assert [m.name for m in taxonomy.modes] == ["copies_example", "vague"]
print(io.shown[0])
# --- output 1/10 (<span id>)
# A storm opens the song as Captain Cole steers the Marlin through black water. ...
# AI note: copies the storm and captain structure of the format example
```

`ws.label_all(workflow, taxonomy=None, *, budget, io=None, yes=False, force=False) -> LabelReport` is
opt-in: the LLM labels every output of the workflow with the taxonomy (default: the last one reviewed for
the workflow). It

1. shows an estimate (outputs, tokens, and USD when the budget has a price) and asks for confirmation
   (`yes=True` skips the question; the CLI's `--yes`),
2. asks the LLM to label the reviewed sample and compares with the person's labels; below 70% agreement it
   refuses (`LabelReport.refused` says why) unless `force=True`,
3. labels the rest, one call per output, until done or the budget is spent.

`budget` is required (pass `None` explicitly for no limit). Labels are stored in the workspace
(`labels` table, source `human` or `llm`).

```python
labels = ws.label_all("song_ideas", budget="500 calls", io=ScriptedIO(["y"]))
print(labels.agreement, labels.labeled, labels.counts)  # 1.0 200 {'(none)': 121, 'copies_example': 79}
assert not labels.refused and labels.labeled == labels.outputs == 200
```

## Stage 5: `explain`

`ws.explain(finding_id) -> Finding` compares the finding's affected runs with the other runs in its scope.
Every run gets features from its recorded inputs: prompt version, each prompt section (id and version),
model, temperature, step parameters and step code version. For each feature the effect is
P(affected | feature) - P(affected | no feature), with a 95% confidence interval; a feature needs at
least 5 runs with it and 5 without. Near-equal effects prefer the most specific kind (sections, then
parameters, model, prompt version, code version), because a section explains the prompt version it
belongs to.

```python
f = ws.explain(f.id)
print(f.cause.kind, f.cause.target, round(f.cause.effect_size, 2), f.cause.status)
# prompt_section format_example 0.79 suspected
print([(c["kind"], c["target"]) for c in f.details["causes"][:2]])
# [('prompt_section', 'format_example'), ('prompt_version', '3')]
print(f.fix.kind, "|", f.fix.description)
```

The top feature becomes the `cause` only when its interval excludes 0; otherwise the cause is
`Cause("none", "")` and `details["notes"]` says no recorded input separates the runs. With an `llm`, the
cause's `hypothesis` is written by the LLM from the top causes, the closeness numbers and sample outputs;
without one it is a sentence built from the numbers. The cause stays `suspected`. Item attributes and
upstream outputs are not compared in v0.1.

## Stage 6: `test`

`ws.test(finding_id, *, variants=3, samples=50, budget=None, quality=None) -> TestResult` replays
affected calls with changed versions of the suspected cause, through the `Replayer`, with everything else
fixed. It runs `explain` first when the finding has no cause yet. In v0.1 only `prompt_section` causes are
replayed, and the metrics that can be measured on replays are the largest-cluster share (needs the
`embedder`: a replayed output counts when its similarity to the cluster centre is >= 0.8), the
truncation rate and the error rate. Other findings get a `TestResult` with a note that says why.

1. A seeded sample of `samples` calls in the finding's scope whose prompt has the section.
2. Variants, in this order, the first `variants` of them: `remove` (the section left out), `rewrite 1..n`
   (alternatives written by the `llm`, only when there is one and `variants > 2`), `counter-instruction`
   (the section plus an instruction not to copy it).
3. Each variant replays every sampled call:
   `replayer.replay_call(span, {"prompt.sections": {section: text_or_None}}, trace=...)`.
4. The metric is measured on the original sample (`metric_before`, so it is the sample's value, not the
   whole finding's) and on each variant's replays; with a `quality(text) -> float | None` scorer, quality
   too.

```python
t = ws.test(f.id, variants=3, samples=20, quality=lambda text: min(len(text) / 200, 1.0))
print(t.confirmed, t.metric_name, t.metric_before, "->", t.metric_after, t.variant)
# True largest_cluster_share 0.8 -> 0.0 remove
print([v["variant"] for v in t.variants], t.calls)
# ['remove', 'rewrite 1', 'counter-instruction'] 61
print(ws.finding(f.id).cause.status, "|", ws.finding(f.id).fix.description)
```

The verdict:

- **confirmed**: the best variant lowers the metric with p < 0.05 (two-proportion test) and quality drops
  by no more than 0.05;
- **refuted**: no variant lowers the metric at all; the cause loses its proposed fix;
- otherwise the cause stays **suspected**.

The `TestResult` is stored on the finding with the cause's new status; a confirmed prompt cause gets a
fix that describes the winning variant and a diff of the section. The finding's own status does not
change: a person confirms it (`ws.set_status(id, "confirmed_by_human")`). Without a `quality` scorer the
result carries a note that quality was not measured (replayed call spans carry no scores).

A wrong cause is refuted. `FakeReplayer()` returns the original output for any change, which is what a
section that does not matter looks like:

```python
wrong = tl.Workspace("demo/lens", embedder=FakeEmbedder.semantic(), replayer=FakeReplayer())
t = wrong.test(f.id, variants=1, samples=10)
print(t.confirmed, wrong.finding(f.id).cause.status)  # False refuted
```

Every `explain` and `test` call starts one trace, shared by all its LLM and replay calls, with
`hone.lens.finding_id` in the trace context. Both accept `trace={"traceparent": ...}` to join a trace of
yours instead.

`budget` limits the LLM rewrites and the replays together; at the limit the test stops with the variants
it finished, and `TestResult.notes` says so. `TestResult.cost_usd` and `TestResult.calls` report the
spend.
