# Implementation decisions

Choices too small for a design change record, made while building v0.1.0. Larger choices are in
[changes/](changes/). Each entry keeps its original number (D-NNN) so older commit messages can be
followed.

## Awaiting owner review

These choices depart from the research's recommendation or from the honeworks defaults. They are
described in the change records and wait for the owner's decision:

| Entry | Choice | Record |
|---|---|---|
| D-001 | numpy is a core dependency (the honeworks default is standard library + pydantic) | [0001](changes/0001-initial-design.md#decision) |
| D-005 | SQLite is the only analytics store; DuckDB (recommended in the research) is not shipped | [0001](changes/0001-initial-design.md#decision) |
| D-010 | one built-in clustering method; no HDBSCAN extra | [0001](changes/0001-initial-design.md#decision) |
| D-016 | the `flow` extra resolves hone-flow from a sibling checkout (uv path source) until hone-flow is published; remove it before publishing | [0002](changes/0002-run-folder-sources.md#decision) |

## Records and ports

### hone-lens writes no spans (D-002)
There is no span sink. Analysis state lives in the workspace database; replays go through the `Replayer`
port with `hone.lens.finding_id` in the trace context. The synthetic test bed has a small store writer
(`testing/_store_writer.py`) that writes the honeworks span-store schema. A sink nobody uses would be dead
code.

### One trace per replay, no context variable (D-006)
hone-lens never receives a trace from a caller's records and has no nested recorded operations: it only
starts one trace per `explain` / `test` call, with `hone.lens.finding_id` (`_tracing.trace_for_finding`).
Both calls accept `trace=` to join a caller's trace instead. A context variable nobody sets would be dead
code.

### Replay calls (D-013, cross-package)
Replays call `Replayer.replay_call(span, {"prompt.sections": {id: text | None}}, trace={"traceparent",
"hone.lens.finding_id"})`, the shape of the `Replayer` port. The honeworks integration tests confirmed
that hone-models' replayer accepts exactly this, and that `hone.lens.finding_id` reaches the spans of every
honeworks package.

### CLI ports and the OpenAI-compatible adapter (D-015, cross-package)
- The `hone-lens` command (typer + rich, `cli` extra) has `ingest`, `analyze`, `review`, `label`
  (`--yes`, `--force`), `explain`, `test`, `report` (`--html`, `--json`), `findings`, `set-status`. Ports
  are named with `--llm` / `--embedder` / `--replayer NAME[:ARG]`, resolved through the entry-point
  groups `hone.text_clients`, `hone.embedders`, `hone.replayers` (the factory is called with `ARG`).
  Exit codes 0 / 1 (hone-lens error, message on stderr) / 2 (usage).
- `hone_lens.adapters.openai` (`openai` extra): `OpenAITextClient(model)` and `OpenAIEmbedder(model)` over
  the OpenAI SDK (>= 3), for any OpenAI-compatible server (OpenAI, Ollama `/v1`, vLLM), registered as the
  `openai` entry points. Structured answers use `response_format=json_schema`; fenced JSON is accepted; an
  answer that is not an object with the schema's required keys is an `error`. Analysis prompts send no
  `max_tokens` cap, because thinking models spend a small cap on reasoning and return empty content; the
  budget still estimates 400 output tokens per call. openai 3.x sends through its own `httpx2`, so the
  contract tests use `httpx2.MockTransport`.
- No hone-models adapter module: hone-models implements the ports structurally and registers its own
  entry points (confirmed by the integration tests, including the `hone.replayers` group).

## Ingest

### Ingest mechanics (D-007)
- Built-in sources have `read_new(cursor) -> (spans, cursor)` besides the port's `spans(since=)`: SQLite
  stores follow `changes.seq` (the mark is read before the spans, so concurrent writes are read next
  time); other files are re-read whole when their `[mtime_ns, size]` changed (run-folder `spans.jsonl`
  files by byte offset, see [0002](changes/0002-run-folder-sources.md)). Any other `RecordSource` is read
  with `since=` the latest start time seen (boundary spans are re-read and deduplicated). Cursors are
  stored per `source.name`, in the same transaction as the spans.
- Deduplication is `INSERT OR IGNORE` on `span_id`: the first copy wins (every honeworks sink writes a
  span once, at span end).
- Derived tables are rebuilt per trace for every trace an ingest touched, so a workflow name recorded by
  hone-flow reaches the model calls recorded by hone-models whatever the ingest order.
- Plain OTel traces: workflow = resource `service.name`, step = parent span name, run = trace. `outputs`
  are model calls with output text that are not scorer calls (`hone.scorer`) and not replays.
- The store is one module, `store.py`.

### Older workspaces gain new columns (D-019)
The analytics tables grow columns as change records land (`seed`, `run_seed`, ...). `schema.create_all`
adds the columns an existing `lens.db` lacks (`ALTER TABLE ... ADD COLUMN`); when a derived table grew,
`AnalyticsDB` rebuilds every trace from the stored spans, and when `flow_steps` grew it clears the cursors
of the `flow:` sources so the next ingest reads their step records again (known spans are still skipped).
No schema version number: the column list itself is the version, and nothing is ever dropped or renamed.

### Phoenix and Langfuse exports (D-014)
- `PhoenixExport(path, workflow=None)`: a `get_spans_dataframe()` export as Parquet (`phoenix` extra,
  pyarrow) or JSONL with flat `attributes.*` columns or an `attributes` object; OpenInference names
  (`llm.model_name`, `llm.token_count.*`, `llm.input_messages`, `llm.invocation_parameters`,
  `input.value` / `output.value`) are mapped to GenAI names.
- `LangfuseExport(path, workflow=None)`: observations as a JSON array, an API page (`{"data": [...]}`) or
  JSONL; ids that are not W3C hex are hashed (SHA-256) into trace / span ids; `GENERATION` observations
  become model calls, `promptName` / `promptVersion` the prompt template and version,
  `calculatedTotalCost` the cost.
- Both exports lack a service name, so the workflow is the `workflow` argument (default: the file stem). A
  changed file is read again whole (ingest deduplicates).

## Analysis

### Stage 1 detectors
(D-008)
- A detector is a plain function `(db: AnalyticsDB) -> list[Finding]`; the built-ins are one table
  (`detectors.DETECTORS`) that `@tl.detector` adds to. `analyze(workflow, since=)` runs every detector
  inside `AnalyticsDB.scoped(...)`, where temporary tables shadow the analytics tables with only the rows
  in scope. A detector that raises is reported in `Report.notes` and skipped.
- Rate findings share one helper (`findings.rate_finding`): at least 3 affected rows and a minimum share
  (1% for failures / truncation, 5% for zero-score spikes and missing scores, 50% gate rejections, 10%
  fallbacks, 20% human overrides, any stale-source resume). Severity comes from the share.
- `regression` compares the runs just before and after every prompt-version, model and step-version
  change: latency / step duration (Mann-Whitney, median up >= 20%), error rate (two-proportion, +2
  points), mean score per scorer (Mann-Whitney, −0.05), at `ALPHA = 0.01`, at least 20 runs a side. α is
  the module constant `hone_lens.detectors.changes.ALPHA` rather than an `analyze` parameter.
- `cost_latency` flags a step with >= max(50%, 1.5 / steps) of a workflow's time or cost, and median
  prompt-token growth >= 50% between consecutive prompt versions.
- Zero-score spikes ([0004](changes/0004-zero-scores-on-discrete-scales.md)): a scorer is on a discrete
  scale when it has fewer than 11 distinct non-zero values and at least two scores per value; then the
  zeros are compared with the lowest non-zero level instead of the scores in (0, 0.2). A zero is the
  judge's answer when it has a non-empty reason, a confidence, no error, and a reason that does not read
  like an error message (`ERROR_LIKE`: starts with "error", "failed", "could not", ..., or mentions a
  traceback, a timeout, invalid JSON, a parse error). Up to three reasons (200 characters each) are kept
  in `details["sample_reasons"]`.
- `setup_smells`: the model family is the model name up to the first `-` / `:` (`gemma4-12b` → `gemma4`).
  A run is seeded when any of its records carries a seed (`hone.flow.seed` on the run or a step span, or
  the manifest's `seed` through `flow:`); repeated prompts with varied seeds in a seeded run are not
  counted ([0003](changes/0003-seeded-best-of-n-is-not-unseeded.md)). Every hone-flow run has a manifest
  seed, so through `flow:` every hone-flow run counts as seeded; `hone:` run folders do not carry it.
- Evidence is up to 20 span ids spread over the affected rows; `scope["time_range"]` is the first and last
  affected start time (not part of the content key).
- Finding ids: `F-0001`... in impact order for new content keys; a known key keeps its id, status and any
  cause, fix and test. `ws.report()` shows every stored, non-dismissed finding of the workflow, including
  ones a later analysis no longer produced.

### The terminal report is plain text (D-009)
The research said "terminal (rich)", but `rich` is only in the `cli` extra and the core must import
without extras. `report/terminal.py` renders plain text; the CLI prints it.

### Stage 2 details (D-010)
The clustering itself is in [0001](changes/0001-initial-design.md#decision). A centre is the output with
the most neighbours at cosine >= 0.8, and clusters need >= max(5, 2%) members; everything else is "no
cluster". 10,000 outputs cluster in well under a second. Homogeneity finding when the largest cluster
holds >= 20% of a step's outputs (baseline: the lowest per-prompt-version share) or fewer than 50% are
distinct; also when the largest-cluster share rose >= 10 points (two-proportion, α 0.01) from the first
prompt version to the last. Mean pairwise cosine and the repeated 4-gram rate are reported. Outliers:
nearest-neighbour similarity below median − 6 × (median − lower quartile). Closeness is computed for the
largest cluster only, against the first recorded text of each section id + version. Embeddings are cached
per (embedder model id, text sha256); a cached vector of another size is embedded again. Cluster ids hash
(workflow, step, centre output), so they are deterministic but can change when new data moves the centre;
a new id is described again. An embedder failure ends stage 2 with a note.

### Budgets and stage 3 (D-011)
- `tl.Budget(usd=, tokens=, calls=, usd_per_1k_tokens=)`; `budget=` also takes a number (USD) or a string
  (`"2usd"`, `"5000 tokens"`, `"40 calls"`). The `TextClient` port reports tokens, not money: a call costs
  the client's `usage["cost_usd"]` when reported, else tokens × `usd_per_1k_tokens` (default 0: local
  models are free, so limit them by tokens or calls). The budget is checked *before* each call with an
  estimate (prompt chars / 4 + 400 output tokens), so a stage overshoots by at most one call's estimate
  error.
- At the limit a stage stops, keeps what it finished and says so (`Report.budget_exhausted`, `notes`,
  `cost_usd`, `llm_calls`). `BudgetExceeded` never escapes `analyze`.
- All analysis prompts go through `llm.ask()`: a system instruction, the data as a JSON payload in the
  last user message, and a JSON Schema whose `title` names the task. Client failures become notes.
- Stage 3 is one call per cluster with an evenly spread sample of 20 outputs (600 characters each),
  returning `description` and `in_common`; largest clusters first; descriptions are stored and not paid
  for again. Hierarchical grouping is not built.

### The scripted analyst answers by schema title (D-003)
`FakeTextClient.analyst()` knows which analysis task a prompt is from the JSON Schema `title` (the
convention above) and answers per task (`testing/analyst.py`); real models get the same schema and a
system instruction. No prompt parsing in the fake.

### Stage 4 review flow (D-012)
- Modules: `coding/sample.py` (stratified sample), `coding/review.py` (notes, taxonomy, human labels),
  `coding/label.py` (`label_all` with the agreement check), `coding/taxonomy.py` (dataclasses),
  `coding/io.py` (`ReviewIO`, `ConsoleIO`).
- The person talks through any object with `show(text)` and `ask(question, default) -> str`. `ConsoleIO`
  never waits without a TTY (every question gets its default); `testing.ScriptedIO` answers from a list.
  Enter accepts the AI's note, mode or suggestion; text replaces a note; a new name renames a mode, `-`
  drops it, `=name` merges it into another; `name: description` adds a mode.
- Sample strata: each cluster, the unclustered outputs, each finding's evidence and three score ranges;
  round-robin over the strata in a seeded order.
- `label_all` asks the LLM again for the human-labeled outputs and refuses below 70% agreement unless
  `force=True`. It shows the token / USD estimate first and labels only after "y" (or `yes=True`, CLI
  `--yes`). Labels are stored as `human` or `llm`.
- Beyond the first API sketch: `review(..., budget=)` and `label_all(workflow, taxonomy=None, *, budget,
  io=None, yes=False, force=False)` (`taxonomy` defaults to the last one reviewed). `review()` without
  `io` refuses to run without a terminal: a taxonomy nobody confirmed would make the agreement check
  meaningless.

### Stage 5 cause ranking and stage 6 replay tests (D-013)
- Findings keep every affected span id (`affected` table); `explain` maps them to runs. Each run in the
  finding's scope gets features: prompt version, each section id + version, model, temperature, step
  params, step code version. Effect = P(affected | feature) − P(affected | no feature) with a 95% CI
  (Newcombe's hybrid score interval, sane at 0% and 100%); a feature needs >= 5 runs with and >= 5
  without; near-equal effects prefer sections > params > model > prompt version > code version. The top
  feature is the cause only when its CI excludes 0; otherwise the cause is `Cause("none", "")` with a
  note. Item attributes and upstream outputs are not compared in v0.1.
- `test` replays a seeded sample of the scope's calls that *have* the section, with variants in this
  order: remove, LLM rewrites (with an LLM and `variants > 2`), section + counter-instruction; the first
  `variants` are used. Replayable metrics: largest-cluster share (a replayed output's cosine to the
  cluster centre >= 0.8), truncation rate, error rate. Confirmed = the best variant lowers the metric with
  p < 0.05 (two-proportion) and quality (an optional `quality(text)` scorer; replayed spans carry no
  scores) drops by <= 0.05; refuted = no variant lowers the metric at all; else suspected. Only
  `prompt_section` causes are replayed; others get a `TestResult` with a note. A refuted cause loses its
  proposed fix. `metric_before` is the value on the replayed sample. Re-running `explain` keeps a tested
  cause's status when it finds the same cause.

### Findings lifecycle and reports (D-014)
- `set_status(id, "fixed")` stores `fixed_at` = the newest ingested span time. A later analysis that finds
  the same content key with affected runs after `fixed_at` sets it to `regressed`; found only in older
  runs, it stays `fixed`. Dismissed findings keep their status and are left out of reports. Notes given
  with a status survive re-analysis.
- `ws.report(workflow, fmt=, path=)` returns the text and writes it when `path` is given. HTML: one file,
  inline CSS, no scripts or external assets; an index, one section per finding, one per evidence cluster
  and one per evidence trace (the span tree with key GenAI fields and outputs); every evidence id links to
  its section, and evidence that is not a stored span is printed as text.

## Test bed and examples

### Synthetic data shape (D-004)
Workflow `song_ideas`, one step `ideas`: `hone.flow.run` → `hone.flow.step` → a generator
`hone.models.chat` (sections `role`, `task`, and `format_example` from prompt v3) and a selection
(`hone.select.run` → `hone.select.score` → a judge `hone.models.chat`, plus `hone.select.decision`). Runs
0–50% use prompt v2, the rest v3 (v4 for the last 25% with the latency plant). The homogeneity plant makes
80% of v3/v4 outputs copy the example (about 40% of all outputs). The OTLP variant has the generator calls
only, with older `gen_ai.*` names (`gen_ai.system`, `gen_ai.usage.prompt_tokens`) to exercise the mapping
layer, one file per seed. Plants carry the evidence a detector needs: truncated calls have prompts near
the 4,096-token limit and `structured.path=repaired`; zero scores come with a judge call whose
`structured.path=failed`; `judge_equals_generator` uses `gemma4-4b` (same family, different model);
`gpu_thrash` adds 2% OOM errors. `TRUTH[plant]["also"]` lists detectors that may legitimately fire too.
The role section is long enough that the v3 example grows the prompt by < 50%, so the clean set has no
token-growth finding. Appended runs (another seed) start at v2.

### The examples set (D-018)
Sixteen files, one per public concept, including `command_line.py` (the installed `hone-lens` command in
a subprocess, as a shell or CI job runs it) and `own_adapters.py` (`TextClient`, `Embedder`, `Replayer` and
`RecordSource` around plain functions, checked with the contract checkers). Each docstring has `What:`,
`How:`, `Why:` and `Run:` paragraphs. `tests/e2e/test_ac20_examples.py` runs every file in a subprocess
whose sockets cannot connect (a `sitecustomize` on `PYTHONPATH`, inherited by the CLI subprocesses),
requires at least two `assert`s per file, the four docstring parts, and an index row for every file and
nothing else. `tests/e2e/test_docs_examples.py` runs the README and `docs/` code blocks.

## Settled by later changes

- hone-flow's first design kept step parameters and review notes in metadata tables even with content
  capture switched off, and hone-lens read those tables. The run-folder redesign
  ([0002](changes/0002-run-folder-sources.md)) removed the tables; hone-lens reads step records through
  hone-flow's read API and never copies review notes.
