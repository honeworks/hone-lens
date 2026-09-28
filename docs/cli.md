# CLI reference

`pip install "hone-lens[cli]"` installs the `hone-lens` command. It drives the same `Workspace` as the
Python API.

To try it on synthetic data, write some runs first:

```python
from hone_lens.testing import synthetic_runs

synthetic_runs("demo", n_runs=300, plant=["truncation", "latency_regression_after_v4"])
```

```bash
hone-lens ingest hone:demo/runs
hone-lens analyze song_ideas --stages stats
hone-lens findings
hone-lens explain F-0001
hone-lens set-status F-0002 dismissed --note "expected"
hone-lens report song_ideas --html report.html
```

## Common options

| Option | Meaning |
|---|---|
| `--workspace`, `-w PATH` | the workspace folder (default `.hone/lens`; created when missing) |
| `--llm NAME[:ARG]` | analysis LLM from the entry-point group `hone.text_clients`, e.g. `openai:gpt-4o-mini` |
| `--embedder NAME[:ARG]` | embedder from `hone.embedders`, e.g. `openai:text-embedding-3-small` |
| `--replayer NAME[:ARG]` | replayer from `hone.replayers` (provided by hone-models or your own package) |
| `--budget TEXT` | LLM budget: `2usd`, `$2`, `2.5`, `"5000 tokens"`, `"40 calls"`. The CLI cannot set a price per token, so a USD limit applies only to clients that report their cost (`usage["cost_usd"]`; the `openai` adapter does not): use tokens or calls otherwise |
| `--json` | print JSON instead of text |
| `--help` | help for the command |

The entry-point factory is called with `ARG` (one string), or with no arguments when there is no `:ARG`.
The `openai` entries read `OPENAI_API_KEY` and `OPENAI_BASE_URL` from the environment; for Ollama, set
`OPENAI_BASE_URL=http://127.0.0.1:11434/v1`. See [adapters.md](adapters.md#entry-points-for-the-cli).

Ports are not stored in the workspace: pass `--embedder`, `--llm` and `--replayer` to each command that
needs them.

## Commands

### `hone-lens ingest SOURCE...`

Read new spans from each source: `hone:<path>`, `otlp:<glob>`, `phoenix:<file>`, `langfuse:<file>`,
`flow:<storage>/<workflow>` (needs `hone-lens[flow]`)
(quote globs so the shell does not expand them: `"otlp:traces/*.json"`). Prints one line per source:
`<source>: read N spans, M new`. Options: `-w`.

### `hone-lens analyze [WORKFLOW]`

Run the analysis stages and print the findings (all workflows when `WORKFLOW` is left out).

| Option | Default | |
|---|---|---|
| `--since TEXT` | none | ISO time, or a duration back from now: `30d`, `12h`, `45m` |
| `--stages TEXT` | `stats,outputs,describe` | comma-separated |
| `--budget TEXT` | unlimited | limits the describe stage |
| `--llm`, `--embedder` | none | without `--embedder` the outputs stage is skipped; without `--llm` the describe stage is skipped (both noted in the output) |
| `--json` | | print the findings as a JSON list |
| `-w` | `.hone/lens` | |

### `hone-lens review WORKFLOW`

Review a sample of outputs with the AI: notes, failure modes, labels (see
[analysis.md](analysis.md#stage-4-review-and-labeling)). Needs a person at a terminal: without one it exits
with an error instead of waiting. Prints the confirmed failure modes. Options: `--sample N` (default 40),
`--budget`, `--llm` (required), `-w`.

### `hone-lens label WORKFLOW`

Label every output with the last reviewed taxonomy (the API's `label_all`). Shows a cost estimate and asks
for confirmation first. Prints `labeled N of M (agreement A): {mode: count}`. Exits 1 when it refuses
(not confirmed, or agreement below 70%).

| Option | |
|---|---|
| `--yes` | do not ask for confirmation |
| `--force` | label even when agreement is below 70% |
| `--budget`, `--llm` (required), `-w` | |

### `hone-lens explain FINDING_ID`

Rank the recorded inputs that explain a finding and print `F-0001: <kind> <target> (<status>)` and the
hypothesis. Options: `--llm` (optional: writes the hypothesis), `--json` (the whole finding), `-w`.

### `hone-lens test FINDING_ID`

Replay-test the finding's suspected cause and print
`F-0001: confirmed; <metric> <before> -> <after> (<variant>)` (or `not confirmed`).

| Option | Default | |
|---|---|---|
| `--variants N` | 3 | |
| `--samples N` | 50 | calls to replay per variant |
| `--budget TEXT` | unlimited | LLM rewrites and replays together |
| `--replayer NAME[:ARG]` | | required |
| `--embedder NAME[:ARG]` | | required to measure diversity findings (the largest-cluster share); use the embedder you analyzed with |
| `--llm NAME[:ARG]` | | optional: LLM-written section variants when `--variants` > 2 |
| `--json` | | the `TestResult` as JSON |
| `-w` | `.hone/lens` | |

### `hone-lens report [WORKFLOW]`

Print the stored findings (dismissed ones left out) as text, or as JSON with `--json`; or write one
self-contained HTML file with `--html PATH` (prints `wrote PATH`). Options: `--html PATH`, `--json`, `-w`.

### `hone-lens findings`

List every stored finding as a table (id, status, severity, category, title). Options:
`--status STATUS` (`new`, `confirmed_by_human`, `dismissed`, `fixed`, `regressed`), `--json`, `-w`.

### `hone-lens set-status FINDING_ID STATUS`

Set a finding's status (`new`, `confirmed_by_human`, `dismissed`, `fixed`, `regressed`); prints
`F-0002: dismissed`. Options: `--note TEXT` (kept with the finding), `-w`. See
[reports.md](reports.md#lifecycle).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | a hone-lens error: unknown source or finding, missing port, unknown status, refused labeling, ...; the message is printed to stderr as `error: ...` |
| 2 | a usage error: unknown option, missing argument, invalid `--status` value for `findings` |
