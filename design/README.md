# Why hone-lens exists

> Point it at 10,000 runs of your AI workflow; get back what's going wrong, why, and which tested change
> fixes it, with evidence.

This folder explains why hone-lens exists and how it is designed. User documentation is in
[`docs/`](../docs/concepts.md); this folder is for people who want to understand, review or change the
design.

## The problem

AI workflows fail quietly. Outputs slowly become repetitive, JSON answers are silently repaired, a judge
scores its own model's work, a context window cuts answers short, a score of `0` really means "could not
score", costs creep up after a prompt change. No single run looks broken, so nobody notices.

These problems only show up **across many runs**, and nobody reads thousands of traces. The workflow that
hone-lens grew out of (OneShotStudio, a local pipeline that writes song ideas, lyrics, music and videos)
had all of the problems above; each was found by hand, late.

## Who it is for

Anyone who runs a generative-AI workflow many times and records it: people using OpenTelemetry GenAI
instrumentation, Phoenix or Langfuse, and people using the other honeworks packages (whose records carry
extra detail such as prompt sections and selection decisions). hone-lens needs no other honeworks package.

## Why existing tools fall short

Each existing tool does part of the job:

- **Trace viewers** (Phoenix, Langfuse) store and show traces well, but a person still has to find the
  pattern in them.
- **Conversation clustering** (Anthropic's Clio, Kura, OpenClio, BERTopic) groups and describes many
  outputs, but does not link a cluster to the input that caused it.
- **Transcript analysis** (Docent) finds and counts behaviours in agent transcripts.
- **Prompt optimizers** (DSPy, GEPA) search for better prompts against a metric, but do not explain what
  was wrong or test a specific hypothesis.

None combines cross-run statistics, output clustering, cause-finding at the level of prompt sections,
replay-tested fixes and a human-confirmed review, for general AI workflows.

## Core ideas

1. **A funnel.** Cheap methods run on everything, expensive ones on small chosen samples: statistics over
   all runs (no model), embeddings of all outputs, then an LLM only for describing clusters, reading
   samples and writing hypotheses, always within a budget.
2. **Findings, not dashboards.** The result is a short ranked list of findings. Each says what is wrong
   and how often, where, the suspected cause, a proposed fix, and the evidence (the spans and clusters
   behind every number).
3. **Correlation is not cause.** A cause stays *suspected* until a replay test (re-running recorded calls
   with only that input changed) confirms or refutes it, and a fix must not trade quality for the metric.
4. **A person decides.** People confirm the failure taxonomy, findings and fixes. hone-lens never changes
   a workflow.
5. **Standard records in, deeper findings with more detail.** It reads standard OpenTelemetry GenAI
   traces from any tool. Richer attributes, such as honeworks' prompt sections, selection scores and
   workflow step statuses, unlock deeper findings, and without them it says what it cannot tell.
6. **Incremental and reproducible.** Only new records are read and embedded; the same data and seeds give
   the same clusters, findings and ids.

## What it deliberately does not do

- It is not a trace store or observability UI: it reads records and writes a static HTML report.
- It never applies a fix on its own.
- It is not a general BI tool.
- Not in this version: multi-step replay tests through a workflow runner, hand-off to a prompt
  optimizer, a watch mode with regression alerts, analysis of image and audio outputs.

## What is in this folder

| File | What it holds |
|---|---|
| [`current.md`](current.md) | the design as it stands today: concepts, rules and the guaranteed behaviour (acceptance cases) |
| [`changes/`](changes/) | one record per design change: what was found, what was decided, why, and how to migrate |
| [`history/`](history/) | the early research the design started from, kept readable |
| [`decisions.md`](decisions.md) | smaller implementation choices, and the ones awaiting owner review |

A new design change starts as a record in `changes/` with status `proposed`; see
[CONTRIBUTING.md](../CONTRIBUTING.md#changing-the-design). How the package was built is described in the
[README](../README.md#how-this-was-built).
