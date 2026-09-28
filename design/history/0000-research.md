# 0000: Early research (September 2026)

This is the research that started hone-lens, rewritten from the original planning brief. It shows where
the design began; [changes/0001](../changes/0001-initial-design.md) records what was then decided, and
[current.md](../current.md) describes the design today.

> **Names used at the time.** The brief used placeholder names: `tracelens` for hone-lens, `flowkit` for
> hone-flow (the workflow runner), `modelkit` for hone-models (model access), `bestofn` for hone-select
> (generate, score, select) and `tastekit` for hone-taste (taste scorers). `ours.*` attributes are
> today's `hone.*` attributes. This page uses today's names.

## Where it came from

The idea came from OneShotStudio, a local pipeline that writes song ideas, lyrics, music and music videos
with local models. In one working session, these problems were found by hand, all of which the recorded
traces could have shown:

| Problem | What should catch it |
|---|---|
| shot-list JSON truncated (prompt + output over a 4,096-token context) | truncation |
| the generator model judging its own ideas and lyrics | setup smell: judge = generator |
| a vision judge without vision (HTTP 400 on every call) | reliability and a capability mismatch |
| lyric scores of 0.000 that were really failures | selection: score spike at 0 / missing |
| random keyframe seeds | setup smell: non-deterministic seed |
| repetitive song ideas (many sea-shanty pirate premises) | homogeneity and closeness to the prompt's example |

These became the first planted issues of the synthetic test bed.

## The pitch

*"Point it at 10,000 runs of your AI workflow; get back what's going wrong, why, and which change fixes
it, with evidence."*

AI workflows fail quietly, the problems only show across many runs, and nobody reads thousands of traces.
Existing tools each do part of the job; none combined cross-run statistics, output clustering,
cause-finding tied to prompt sections, replay-tested fixes and a human-confirmed workflow.

## The funnel

The central idea from the start: cheap methods on **all** runs, expensive ones (LLMs reading traces) on
**small, chosen samples**.

```text
10,000 runs
  │ 1. statistics over everything (no LLM): failure / retry / repair rates, latency, tokens, cost,
  │    reuse, GPU swaps, scores, by workflow, step, prompt version, section set, model, params, time
  │ 2. embed every output (cheap, incremental): similarity, clusters, homogeneity,
  │    closeness to prompt examples
  │ 3. AI describes each cluster / anomaly (Clio / Kura style, hierarchical)
  │ 4. AI reads samples in depth (~50–200 traces, stratified) → open-coding notes →
  │    failure taxonomy (a person confirms) → LLM labels all runs
  │ 5. find the cause: compare affected runs with the others on every recorded input
  │ 6. test the cause: replay a few dozen inputs with the suspected part changed
  ▼
Report: ranked findings, evidence links, suggested fix, measured effect, confidence
```

The methods behind the stages:

- **Stage 1** had nine detector families: reliability, truncation, cost and speed, GPU, cache (later
  reuse, see [0002](../changes/0002-run-folder-sources.md)), selection, human override, setup smells and
  regressions. "This got worse when X changed" was expected to be the most common finding.
- **Stage 2** borrowed from research on mode collapse, which uses average cosine similarity between
  responses as the main proxy for diversity, and added **closeness to prompt sections**: the link
  between "outputs look alike" and "they copy the example".
- **Stage 3** followed Anthropic's Clio and its open versions (Kura, OpenClio): short summaries, then
  cluster descriptions.
- **Stage 4** followed the error-analysis method of Hamel Husain and Shreya Shankar (open coding, axial
  coding, counting) and MAST's practice of checking an LLM labeler against human labels before trusting
  its counts.
- **Stage 5** ranks recorded inputs (prompt version, which sections were present and their versions,
  model, params, item properties, upstream outputs) by effect, with an LLM hypothesis, always labeled
  *suspected*.
- **Stage 6** is a counterfactual replay: variants of the suspected cause (remove, replace, vary, add a
  counter-instruction), everything else fixed, measuring the finding's metric **and** quality, so a fix
  never trades quality for the metric (Goodhart). Optionally, confirmed issues would go to a prompt
  optimizer (GEPA-style), still verified by replay.

## Best practices it set out to enforce

1. Funnel: statistics and embeddings on everything, LLM reading on samples.
2. Evidence for every claim, linked to traces.
3. Correlation is not cause: causes stay suspected until a replay test confirms them.
4. Test fixes on quality too.
5. Never auto-apply changes: a person approves, then regressions are watched.
6. People confirm the taxonomy; the LLM labeler is checked against human labels first.
7. Compare across versions and time.
8. Budgets and cost reports for every LLM stage and test.
9. Privacy: respect content-capture settings, never record secrets.
10. Incremental and reproducible.

## The person's role

| Stage | The person | Typical effort |
|---|---|---|
| taxonomy | accept / edit AI notes on ~30–50 traces; confirm 5–8 failure modes | 30–60 minutes once per workflow |
| findings | confirm or dismiss high-severity findings | minutes |
| fixes | approve a tested fix before it is applied | minutes |
| quality checks | a few picks when replay results are close | a handful of picks |

## What it needed from the recording side

hone-lens reads records; it does not make them. The research listed what the other packages should
record for the deeper findings: prompts as template id + version + named sections with their own versions
(for section-level causes), exact model versions, params, seeds, finish reasons and repair paths, a replay
of recorded calls with one section changed, per-step system metrics and step versions, candidate / score /
decision records, and OpenTelemetry field names with shared trace ids everywhere. Standard OpenTelemetry
GenAI traces alone had to be enough for the basic findings, so that hone-lens is useful without any other
honeworks package.

## Scale and cost

- Never run an LLM over all runs by default; labeling everything is opt-in with a cost estimate first.
- Incremental ingest and embedding; periodic re-clustering.
- Every LLM stage and replay test takes a money / token / time budget and reports its cost.
- A local analytics store (DuckDB or SQLite) and a vector index; 10,000 runs of several steps is small.

## Scope as first planned

- **v1:** ingest OTel GenAI traces from any source and the honeworks stores, incrementally; stages 1–6
  (replay of single model calls); findings with status tracking; terminal, JSON and static HTML reports;
  custom detectors; tests on synthetic runs with planted issues.
- **v2:** multi-step replay through the workflow runner, prompt-optimizer hand-off, a watch mode with
  regression alerts, a web view, image and audio output analysis.
- **Non-goals:** a trace store or observability backend, an automatic self-modifying system, a general BI
  tool.

## Open questions at the time, and how they were answered

| Question | Recommended then | Outcome |
|---|---|---|
| Analytics store | DuckDB | SQLite, after measuring DuckDB's insert speed ([0001](../changes/0001-initial-design.md)) |
| Clustering approach | embed then cluster, LLM summaries of samples | as recommended, with one built-in clustering method instead of HDBSCAN ([0001](../changes/0001-initial-design.md)) |
| Default analysis model | local, through the model-access package | any `TextClient`; an OpenAI-compatible adapter ships (covers Ollama); hone-models implements the port |
| Depend on Docent | learn from it | learned from it; no dependency |
| Build order | last, after the runner and model access | built in parallel against the synthetic test bed and the shared record format |
| Name | `tracelens` (placeholder) | `hone-lens` |
| License / Python | MIT or Apache-2.0; Python ≥ 3.11 | Apache-2.0; Python ≥ 3.11 |

## Projects learned from

| Project | What was taken |
|---|---|
| Docent (Transluce) | AI-assisted analysis of large transcript collections; measuring how often a behaviour occurs |
| Kura, OpenClio (open versions of Anthropic's Clio) | per-item summaries, then clustering and cluster descriptions |
| BERTopic | clustering many short texts |
| GEPA, DSPy (MIPROv2) | trace-reading prompt diagnosis and metric-driven prompt search (a later hand-off target) |
| MAST | a failure taxonomy from annotated traces; an LLM labeler checked against experts |
| Hamel Husain / Shreya Shankar error analysis | open coding, axial coding, counting; the human / AI split |
| Arize Phoenix, Langfuse | trace storage and export formats hone-lens reads |

## References

- [Docent](https://transluce.org/docent) · [Analyzing long agent transcripts](https://www.lesswrong.com/posts/Mj276hooL3Mncs3uv/analyzing-long-agent-transcripts-docent)
- [Anthropic Clio](https://www.anthropic.com/research/clio) · [Clio paper (arXiv 2412.13678)](https://arxiv.org/pdf/2412.13678) · [OpenClio](https://github.com/Phylliida/OpenClio) · [Kura](https://github.com/jxnl/kura)
- [BERTopic clustering](https://maartengr.github.io/BERTopic/getting_started/clustering/clustering.html)
- [GEPA](https://github.com/gepa-ai/gepa)
- [MAST: why multi-agent LLM systems fail](https://arxiv.org/abs/2503.13657)
- [Hamel Husain: error analysis](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html)
- [Arize Phoenix](https://github.com/arize-ai/phoenix)
