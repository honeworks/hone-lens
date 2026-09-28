# Examples

Each file shows how one thing is done in hone-lens, and is the reference to copy from. Every file opens
with a docstring that says **what** it shows, **how** (the calls, in order) and **why** (the problem it
solves), then runs top to bottom offline: synthetic runs (`hone_lens.testing.synthetic_runs`) and the
public fakes stand in for real records and models, everything goes into a temporary folder, and `assert`s
check the key facts. The test suite runs every file (`tests/e2e/test_ac20_examples.py`).

```bash
python examples/quickstart.py
```

Read them in this order (the last column is the section of [design/current.md](../design/current.md) each
example illustrates):

| # | File | Concept | In one sentence | Design |
|---|---|---|---|---|
| 1 | [quickstart.py](quickstart.py) | the whole funnel | Ingest, analyze, explain, replay-test and report 2,000 synthetic runs. | §3 |
| 2 | [ingest_sources.py](ingest_sources.py) | sources and incremental ingest | Read honeworks stores, OTLP/JSON, Phoenix and Langfuse exports into one workspace, then only what is new. | §4 stage 0 |
| 3 | [flow_run_folders.py](flow_run_folders.py) | hone-flow runs | Read run folders through `hone:` and step records through `FlowRuns` (`flow:`), with statuses, attempts and reuse. | §4 stage 0 |
| 4 | [plain_otel.py](plain_otel.py) | plain OpenTelemetry traces | Analyze traces with no `hone.*` attributes and see what the findings say is missing. | §4 stage 0, AC-3 |
| 5 | [stats_detectors.py](stats_detectors.py) | stage 1 detectors | Find countable problems without a model and follow each finding to its evidence spans. | §4 stage 1 |
| 6 | [custom_detector.py](custom_detector.py) | your own detector | Register a rule with `@tl.detector`; its findings behave like the built-in ones. | §2, AC-10 |
| 7 | [output_clusters.py](output_clusters.py) | stage 2 output analysis | Embed and cluster outputs, measure homogeneity and closeness to prompt sections. | §4 stage 2 |
| 8 | [describe_with_budget.py](describe_with_budget.py) | stage 3 descriptions and budgets | Let an LLM describe each cluster within a `Budget`, and see what was spent. | §4 stage 3, AC-6 |
| 9 | [review_with_scripted_io.py](review_with_scripted_io.py) | stage 4 review, taxonomy, labeling | Build a failure taxonomy with the AI and a person, then label every output. | §4 stage 4, AC-7 |
| 10 | [explain_cause.py](explain_cause.py) | stage 5 causes | Rank the recorded inputs that separate affected runs and get a suspected cause and fix. | §4 stage 5 |
| 11 | [replay_test.py](replay_test.py) | stage 6 replay tests | Replay calls with the suspected section changed through a `Replayer`; confirm or refute. | §4 stage 6, AC-1, AC-11 |
| 12 | [findings_lifecycle.py](findings_lifecycle.py) | findings lifecycle | Confirm, dismiss and fix findings; re-analysis keeps ids and re-checks fixes. | §5, AC-8 |
| 13 | [reports.py](reports.py) | reports | Render the findings as terminal text, JSON and one self-contained HTML file. | §5, AC-9 |
| 14 | [command_line.py](command_line.py) | the `hone-lens` command | Run the funnel from a shell with `--json` output and exit codes. | §2 CLI |
| 15 | [own_adapters.py](own_adapters.py) | bring your own models | Implement `TextClient`, `Embedder` and `Replayer` around plain functions and check them. | §2, §6 |
| 16 | [step_rerunner.py](step_rerunner.py) | step reruns as hone-flow forks | Rerun a workflow step for some items with `FlowStepRerunner`, the `StepRerunner` port. | §4 stage 0, AC-18 |
