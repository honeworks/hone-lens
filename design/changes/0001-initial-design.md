# 0001: Initial design (v0.1)

## Status

`implemented in 0.1.0`. The parts about reading hone-flow records were revised by
[0002](0002-run-folder-sources.md) before the release.

## Context

hone-lens comes out of OneShotStudio, a local pipeline that generates song ideas, lyrics, music and music
videos. Over one working session, these problems were found in it by hand, each late and each invisible in
any single run:

- shot lists truncated because prompt plus output exceeded a 4,096-token context window;
- the judge model was the generator model, scoring its own work;
- a model without vision was used as the vision judge (HTTP 400 on every call);
- lyric scores of `0.000` that really meant "failed to score";
- keyframe seeds that changed per process (Python's randomized `hash()`);
- song ideas that kept repeating one premise, because the prompt's format example was being copied.

All of them were visible in the recorded traces, but only across many runs. The early research for this
design is in [history/0000-research.md](../history/0000-research.md).

## Problem

Nobody reads thousands of traces. Existing tools each cover a piece (trace viewers, conversation
clustering, transcript analysis, prompt optimizers), but none goes from "many runs" to "this is wrong, this
input causes it, and this tested change fixes it" for a general AI workflow, with a person confirming the
conclusions.

## Options

The main choices, each with the alternatives considered:

| Question | Options | Chosen |
|---|---|---|
| Where analysis effort goes | an LLM reads every run; **a funnel**: statistics and embeddings on everything, an LLM on samples | funnel |
| Inputs | our own packages' stores only; **standard OpenTelemetry GenAI traces from any tool**, with our own attributes as a bonus | any OTel GenAI source |
| Clustering | LLM summaries of every output first (accurate, costly); **embed then cluster**, LLM descriptions of clusters (Clio / Kura style) | embed then cluster |
| Causes | report correlations; **rank recorded inputs by effect and test the top one by replay** | rank, then replay |
| Human role | fully automatic; **the AI proposes, a person confirms taxonomy, findings and fixes** | person confirms |
| Analytics store | DuckDB (fast aggregations; recommended in the research); SQLite (standard library, what the other packages use) | SQLite (below) |
| Clustering library | HDBSCAN when installed, with a built-in fallback; **one built-in method** | one built-in method (below) |
| Vector math | pure Python; numpy in an extra with a pure-Python fallback; **numpy in core** | numpy in core (below) |
| Depend on Docent | build on it; export to it; **learn from it** | learn from it |

## Decision

hone-lens v0.1 is a workspace (one folder, one SQLite file) with an ingest step and six analysis stages,
as described in [current.md](../current.md):

0. **Ingest** from `RecordSource`s (honeworks span stores, OTLP JSON, Phoenix and Langfuse exports), with a
   mapping layer for older GenAI names and non-honeworks traces, incremental per-source positions and
   `span_id` deduplication, into derived `calls` / `steps` / `selections` / `outputs` tables.
1. **Statistics detectors** without a model, plus `@tl.detector` for custom ones.
2. **Output analysis**: embeddings, clustering, homogeneity, closeness to prompt sections, outliers.
3. **Cluster descriptions** by an LLM within a budget.
4. **Review** with a person: AI notes, an AI-proposed taxonomy the person confirms, human labels, and
   opt-in LLM labeling of all outputs after an agreement check.
5. **Cause ranking** over recorded inputs with confidence intervals and an LLM hypothesis.
6. **Replay tests** through a `Replayer` port that confirm or refute a prompt-section cause, with a
   quality guard.

Findings carry evidence, stable ids and a lifecycle (dismissed stays dismissed; fixed findings are
re-checked for regressions), and render as terminal text, JSON or a self-contained HTML file. hone-lens
owns the ports it needs (`RecordSource`, `TextClient`, `Embedder`, `Replayer`) and ships a fake and a
contract checker for each, plus a synthetic planted-issue generator that serves as the test bed: every
OneShotStudio problem above is one plant with a known ground truth.

Three choices differ from what the research recommended or from the family's defaults. All three are
**awaiting owner review**:

- **numpy is a core dependency.** Stage 2 needs cosine similarity, clustering and pairwise homogeneity over
  up to 10,000 outputs of 768-dimension embeddings in under a minute. Pure-Python dot products are about
  100 times too slow for that, and a fallback path would double the clustering code. numpy is ubiquitous
  and has no dependencies of its own.
- **SQLite is the only analytics store; no DuckDB.** Measured on the development machine, DuckDB's
  `executemany` of 70,000 span rows took 160 s against SQLite's 0.06 s. A fast DuckDB path needs
  Arrow / pandas bulk loading (more code, more dependencies), and SQLite meets the 10,000-run target.
  `AnalyticsDB` stays one small class, so a DuckDB version can be added later without API changes.
- **One built-in clustering method; no HDBSCAN extra.** A deterministic density ("leader") clustering on a
  seeded sample of at most 2,000 outputs, with every other output joining its nearest centre when close
  enough. One code path to test and explain; two back ends would give different clusters for the same data
  depending on what is installed, which breaks determinism. An HDBSCAN option can be added later behind
  the same `cluster()` signature.

hone-lens writes no spans of its own: its analysis state lives in the workspace, and replays are recorded
by the replayer with `hone.lens.finding_id` in the trace context.
Smaller implementation choices (thresholds, statistics, ingest mechanics, the review flow) are listed
in [decisions.md](../decisions.md).

## Consequences

- Every run is covered by statistics and embeddings at low cost; LLM spending is bounded by budgets and
  reported.
- People who use no other honeworks package can use hone-lens on their own OpenTelemetry traces;
  honeworks attributes make the findings deeper (section-level causes, score spikes, GPU thrash).
- Fixes are only claimed after a replay test, and never applied automatically.
- The core is heavier than the family's "standard library + pydantic" rule (numpy).
- SQLite limits very large analytical queries; the target (10,000 runs) takes about 10 s for stages 1–2.
- Multi-step replay, prompt-optimizer hand-off, watch mode and non-text outputs are left for later.

## Migration and compatibility

None: this is the first release.
