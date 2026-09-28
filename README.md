# hone-lens

[![CI](https://github.com/honeworks/hone-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/honeworks/hone-lens/actions/workflows/ci.yml)

Analyze thousands of AI workflow runs to find issues, their causes and replay-tested fixes.

Part of **[honeworks](https://github.com/honeworks)**: small, standalone tools for reliable generative-AI
workflows. Works on its own; works better with its siblings.

## Why

Once an AI workflow runs thousands of times, reading traces one by one stops working: the problems that
matter (outputs that all look alike, calls cut off at the context limit, one prompt version that needs
repairs, a model that got worse) only show up across many runs, and a dashboard tells you *that*
something changed, not *why* or *what to do*. hone-lens reads the traces your AI workflows already
record (OpenTelemetry GenAI traces as OTLP JSON, Phoenix and Langfuse exports, honeworks span stores) and
turns thousands of runs into a short list of **findings**: what goes wrong and how often ("40% of
`song_ideas` outputs share one pattern"), the recorded input that most likely causes it (a prompt section,
a model, a parameter), a proposed fix, and, when you ask for it, a replay test that measures whether the
fix helps. hone-lens never changes your workflow: it reports, and a person decides.

## Features

- **Reads what you already record**: OTLP JSON files, Phoenix Parquet exports, Langfuse exports,
  honeworks span stores and hone-flow run folders; ingest is incremental.
- **Statistics detectors with no model at all**: failure, retry and repair rates, truncation, time and
  cost hot spots, GPU thrash, score spikes and gate rejections, human overrides, setup smells, and
  regressions after a prompt, model or step change; add your own with `@tl.detector`.
- **Output analysis**: embed and cluster outputs, measure homogeneity and closeness to prompt sections.
- **LLM descriptions, review and labeling within a budget**: a `Budget` (USD, tokens or calls) caps the model
  calls; a person reviews the proposed failure taxonomy.
- **Causes**: rank the recorded inputs (prompt sections, models, parameters) that separate affected runs.
- **Replay tests**: re-run the recorded calls with the suspected cause changed and confirm or refute it.
- **Findings lifecycle and reports**: stable finding ids across re-analysis, statuses, and terminal, JSON
  and self-contained HTML reports; a `hone-lens` command for all of it.
- **Bring your own models**: small `TextClient`, `Embedder` and `Replayer` protocols, OpenAI-compatible
  adapters included, deterministic fakes in `hone_lens.testing`.

## Install

```bash
pip install hone-lens                 # or: uv add hone-lens
pip install "hone-lens[cli,openai]"   # with extras
```

Until hone-lens is on PyPI, install it from GitHub:

```bash
pip install "git+https://github.com/honeworks/hone-lens"
uv add "hone-lens @ git+https://github.com/honeworks/hone-lens"
```

For the `flow` extra before hone-flow is on PyPI, install hone-flow the same way first:
`pip install "git+https://github.com/honeworks/hone-flow"`.

Python 3.11 or newer. The core needs only numpy and pydantic; everything else is an extra:

| Extra | Adds | Installs |
|---|---|---|
| `cli` | the `hone-lens` command | typer, rich |
| `openai` | OpenAI-compatible LLM and embedder adapters (OpenAI, Ollama, vLLM, ...) | openai |
| `phoenix` | reading Phoenix Parquet exports | pyarrow |
| `flow` | reading hone-flow runs through its API (any storage, with step records) and forking runs | hone-flow |

## Quickstart

This runs offline, with no other package: it writes 300 synthetic runs with two planted issues, then uses
the deterministic fakes from `hone_lens.testing` in place of a real embedder, LLM and replayer.

```python
import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, synthetic_runs

runs = synthetic_runs("demo", n_runs=300, plant=["homogeneity_from_example", "truncation"])
replayer = FakeReplayer.removing_section_reduces_similarity()
ws = tl.Workspace("lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst(), replayer=replayer)
ws.ingest(f"hone:{runs.root}")
report = ws.analyze("song_ideas")  # 4 findings, most important first
f = ws.explain(next(f.id for f in report.findings if f.category == "diversity"))  # suspected cause
t = ws.test(f.id, variants=2, samples=20)  # replay without that prompt section: t.confirmed is True
ws.report("song_ideas", fmt="html", path="report.html")
print(ws.report("song_ideas"))
```

The printed report starts like this (span ids differ):

```text
hone-lens: song_ideas
=====================
4 findings

F-0001  [high] diversity  39% of song_ideas/ideas outputs share one pattern (black, captain, intro, ...)
        116/300 affected  step=ideas  status=new
        cause (confirmed): prompt_section format_example  Affected runs share prompt_section ...
        fix: remove the 'format_example' prompt section
        test (confirmed): largest_cluster_share 0.95 -> 0.0
```

`report.html` is one self-contained file: every finding links to the traces and clusters behind it.

### With your own tools

Point hone-lens at your own traces and models. Here: OTLP JSON files from an OpenTelemetry Collector file
exporter, and a model behind any OpenAI-compatible server (`pip install "hone-lens[openai]"`).

<!-- not-run -->
```python
import hone_lens as tl
from hone_lens.adapters.openai import OpenAIEmbedder, OpenAITextClient
from hone_lens.sources import OtlpJsonFiles

ws = tl.Workspace(
    ".hone/lens", llm=OpenAITextClient("gpt-4o-mini"), embedder=OpenAIEmbedder("text-embedding-3-small")
)
ws.ingest(OtlpJsonFiles("traces/*.json"))  # or the string "otlp:traces/*.json"
# The adapter reports tokens, not money: give a price so a USD limit can apply.
report = ws.analyze("my-service", budget=tl.Budget(usd=2.0, usd_per_1k_tokens=0.0006))
print(ws.report("my-service"))
```

The workflow name of plain OpenTelemetry traces is the resource `service.name`. For Ollama, pass
`base_url="http://127.0.0.1:11434/v1"` to both adapters.

## Use it with the rest of honeworks

- **Records.** hone-models and hone-select write span stores under `.hone/<package>/spans.db`;
  hone-flow keeps each run's spans in its run folder (`<storage>/<workflow>/runs/<run_id>/spans.jsonl`).
  `ws.ingest("hone:.hone")` reads the stores, and `ws.ingest("hone:<storage>/<workflow>/runs")` reads
  local run folders, incrementally. Their `hone.*` attributes (prompt sections, selection scores and
  gates, workflow step statuses, attempts, reuse / fork provenance, GPU leases) unlock the deeper
  findings: section-level causes, score spikes, GPU thrash, stale-source resumes. With the `flow` extra,
  `ws.ingest("flow:s3://bucket/prefix/<workflow>")` reads hone-flow runs through hone-flow's read API
  (any storage it supports) together with their step records: attempts, review decisions and
  reject-and-revise cycles ([docs/sources.md](https://github.com/honeworks/hone-lens/blob/main/docs/sources.md#hone-flow-runs)).
- **Ports.** hone-lens' core depends on no other honeworks package. It talks to models through three small
  protocols it owns, `TextClient`, `Embedder` and `Replayer` (see [docs/adapters.md](https://github.com/honeworks/hone-lens/blob/main/docs/adapters.md)),
  and to hone-flow through a fourth, `StepRerunner`, which the `flow` extra implements as a fork of a run
  (`FlowStepRerunner(workflow)`; reserved for multi-step replay tests).
  hone-models implements them, so its clients can be passed to `Workspace(...)` directly, and its
  `Replayer` re-runs recorded calls for replay tests. Replayed calls carry `hone.lens.finding_id` in their
  trace context, so they are recorded like any other call and can be traced back to the finding.
- **Entry points.** The CLI finds implementations by name in the entry-point groups `hone.text_clients`,
  `hone.embedders` and `hone.replayers`. hone-lens registers `openai` in the first two; installed packages
  such as hone-models add their own (`--llm NAME:MODEL`, `--replayer NAME:ARG`).

## Command line

```bash
hone-lens ingest hone:.hone "otlp:traces/*.json"
hone-lens analyze song_ideas --since 30d --embedder openai:text-embedding-3-small --llm openai:gpt-4o-mini
hone-lens findings --status new
hone-lens explain F-0001
hone-lens test F-0001 --variants 3 --samples 50 --budget 2usd --replayer NAME:ARG --embedder openai:text-embedding-3-small
hone-lens report song_ideas --html report.html
hone-lens set-status F-0003 dismissed --note "expected"
```

Every command takes `--workspace/-w` (default `.hone/lens`). Exit codes: 0 success, 1 a hone-lens error
(message on stderr), 2 a usage error. Full reference: [docs/cli.md](https://github.com/honeworks/hone-lens/blob/main/docs/cli.md).

## Documentation

- [Concepts](https://github.com/honeworks/hone-lens/blob/main/docs/concepts.md): workspace, sources, the six stages, findings, budgets
- [Sources](https://github.com/honeworks/hone-lens/blob/main/docs/sources.md): honeworks stores, OTLP JSON, Phoenix, Langfuse, your own source
- [Detectors](https://github.com/honeworks/hone-lens/blob/main/docs/detectors.md): the built-in detectors and writing your own
- [Analysis](https://github.com/honeworks/hone-lens/blob/main/docs/analysis.md): outputs, descriptions, review, labeling, causes, replay tests
- [Reports](https://github.com/honeworks/hone-lens/blob/main/docs/reports.md): terminal, JSON, HTML; the findings lifecycle
- [Bring your own adapter](https://github.com/honeworks/hone-lens/blob/main/docs/adapters.md): `TextClient`, `Embedder`, `Replayer`, `RecordSource`
- [CLI reference](https://github.com/honeworks/hone-lens/blob/main/docs/cli.md)
- [Records reference](https://github.com/honeworks/hone-lens/blob/main/docs/records.md): what hone-lens reads and writes
- [examples/](https://github.com/honeworks/hone-lens/blob/main/examples/README.md): one runnable, explained example per concept, in reading order
- [Design](https://github.com/honeworks/hone-lens/blob/main/design/README.md): why hone-lens exists, the [current design](https://github.com/honeworks/hone-lens/blob/main/design/current.md), and the
  [design changes](https://github.com/honeworks/hone-lens/tree/main/design/changes/) with their reasons
- [Contributing](https://github.com/honeworks/hone-lens/blob/main/CONTRIBUTING.md)

## How this was built

hone-lens was specified by a human and built by AI coding agents (Claude) working against written
specifications and acceptance tests; a human reviewed the decisions they made, and commits written with
AI carry a `Co-Authored-By` line. Every design change, with what was found, what was decided and why, is
in [design/changes/](https://github.com/honeworks/hone-lens/tree/main/design/changes/); the smaller implementation choices,
including those still awaiting the owner's review, are in [design/decisions.md](https://github.com/honeworks/hone-lens/blob/main/design/decisions.md).

## Status

Alpha, version 0.1.0. The public API follows [design/current.md](https://github.com/honeworks/hone-lens/blob/main/design/current.md) and may still change
before 1.0; changes are listed in the [CHANGELOG](https://github.com/honeworks/hone-lens/blob/main/CHANGELOG.md).

## License

Apache-2.0 ([LICENSE](https://github.com/honeworks/hone-lens/blob/main/LICENSE)). Copyright 2026 Bahman Shadmehr.
