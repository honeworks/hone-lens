# Detectors

Stage 1 of `analyze` runs **detectors**: plain functions that read the analytics tables and return
findings. They need no model. Stage 2 (output analysis) adds findings of its own, listed at the end.

```python
import hone_lens as tl
from hone_lens.testing import synthetic_runs

runs = synthetic_runs("demo", n_runs=400, plant=["truncation", "latency_regression_after_v4"])
ws = tl.Workspace("demo/lens")
ws.ingest(f"hone:{runs.root}")
report = ws.analyze("song_ideas", stages=("stats",))
for f in report.findings:
    print(f.id, f.detector, "|", f.title)
assert {"truncation", "regression"} <= {f.detector for f in report.findings}
```

## Built-in detectors

A rate finding needs at least 3 affected rows (1 for a stale-source resume) and the minimum share below.
Severity comes from the affected share. Evidence is up to 20 span ids spread over the affected rows.

| Detector | Category | Finds | Grouped by | Fires when | Reads |
|---|---|---|---|---|---|
| `failure_rate` | reliability | failed calls (`error_rate`), retried calls (`retry_rate`), structured output that needed a retry, repair or failed (`repair_rate`), failed step runs (`step_error_rate`; only steps that ran count: skipped, blocked, paused and fork-copied steps are neither failures nor successes) | workflow, step, model, scorer, prompt version (steps: workflow, step) | share >= 1% | `calls`, `steps` |
| `truncation` | reliability | calls cut off with `finish_reason=length` (`truncation_rate`); counts those whose prompt was within 10% of the context limit and those repaired | workflow, step, model | share >= 1% | `calls` |
| `cost_latency` | cost | a step that takes most of a workflow's time or cost (`time_share`, `cost_share`); median prompt tokens growing between consecutive prompt versions (`median_input_tokens`) | workflow; workflow, step, scorer | share >= max(50%, 1.5 / number of steps); growth >= 50% | `steps`, `calls` |
| `gpu_thrash` | cost | model swaps per run, slow GPU leases, out-of-memory errors (`model_swaps_per_run`) | workflow | >= 0.5 swaps per run, median lease wait >= 1 s, or any OOM error | `calls` (hone-models GPU attributes) |
| `reuse_health` | reliability | resumed hone-flow runs that continued a step whose source changed without a version bump (`stale_source_rate` = runs with hone-flow's `source_changed_version_unchanged` warning / resumed runs) | workflow, step | any such run | `run_calls` (warning events on `hone.flow.run`) |
| `selection_health` | quality / reliability | scores spiking at exactly 0 while few are just above it (in (0, 0.2), or at the next level up on a discrete scale such as 1-5 mapped to 0-1): zeros without the judge's answer (no reason, no confidence, an error or an error-like reason) as possibly failed scores (`zero_score_rate`), zeros the judge explained as floor scores with sample reasons in `details` (`floor_score_rate`), missing scores (`none_score_rate`), gate rejections (`gate_rejection_rate`), fallbacks (`fallback_rate`), thin winner margins (`low_margin_rate`), judges that disagree (`judge_disagreement_rate`) | workflow, scorer / gate | 5% zeros or missing (floor scores: medium from 10%, high from 30%); 50% rejected; 10% fallbacks; 30% margins < 0.02; 20% of multi-judged candidates > 0.5 apart | `selections`, `calls` (hone-select attributes) |
| `human_override` | quality | people rejecting, editing or overruling at a human gate (`override_rate`); items a person sent back so the step ran again (`revision_rate`, with the number of reject-and-revise cycles in `details`); items that passed an automated reviewer (`actor_kind = "automated"`) only after reject-and-revise rounds (`automated_rework_rate`, with `rounds_until_approved`, `mean_rounds`, `max_rounds` in `details`; low or medium only) | workflow, step, gate; workflow, step | >= 5 decisions and share >= 20%; >= 3 revised items and share >= 20%; >= 3 reworked items and share >= 20% (medium from 50%) | `selections` (hone-flow gate spans), `flow_steps` |
| `setup_smells` | setup | a judge from the same model family as the generator (`same_family_judge_share`); capability errors such as HTTP 400 on images (`capability_error_rate`); the same prompt sent repeatedly with other random seeds, in a run that records no seed (`unseeded_repeat_rate`) | workflow, scorer, model / step | any same-family judge; any capability errors; >= 3 repeated prompts with varied seeds in a step without a `seed` param, in runs without a recorded seed (`hone.flow.seed` on the run or step span, or the manifest's `seed` through `flow:`) | `calls` |
| `regression` | regression | median latency or step duration up >= 20% (`latency_ms_median`), error rate up >= 2 points (`error_rate`), mean score down >= 0.05 (`mean_score`) right after a prompt-version, model or step-version change | workflow, step (and scorer) | p < `ALPHA` (0.01) with >= 20 runs on each side | `calls`, `steps`, `selections` |

hone-flow has no cross-run cache: results are reused only by an explicit fork (recorded as
`reused_from` / `fork_of`), and a resume that continues a step whose code changed without a version bump
gets a warning. `reuse_health` reports those warnings; reused (copied) steps are left out of failure rates
and durations, since no work was done.

The model family is the model name up to the first `-` or `:` (`gemma4-12b` and `gemma4-4b` are both
`gemma4`). The regression significance level is the module constant
`hone_lens.detectors.changes.ALPHA`; set it before `analyze` to change it. `regression` findings record
the version boundary in `details["boundary"]`.

A detector that raises is skipped and reported in `Report.notes`; the other detectors still run.

## Output findings (stage 2)

With an embedder, `analyze` also produces these (detector names as shown):

| Detector | Category | Finds |
|---|---|---|
| `homogeneity` | diversity | the largest cluster of a step's outputs holds >= 20% of them (`largest_cluster_share`), or rose >= 10 points from the first prompt version to the last; fewer than 50% of outputs are distinct (`distinct_rate`) |
| `outliers` | quality | outputs far from all others (`outlier_rate`) |

See [analysis.md](analysis.md#stage-2-outputs).

## Writing your own detector

A detector is a function `(db: tl.AnalyticsDB) -> list[tl.Finding]`. Register it with `@tl.detector`;
`analyze` runs it after the built-in ones, on the same slice of data.

```python
import hone_lens as tl
from hone_lens.testing import synthetic_runs


@tl.detector(category="quality")
def ideas_too_long(db: tl.AnalyticsDB) -> list[tl.Finding]:
    """Song ideas longer than 150 characters, per workflow and step."""
    found = []
    for scope in db.query("SELECT DISTINCT workflow, step FROM outputs"):
        rows = db.query(
            "SELECT span_id, start_time FROM outputs WHERE workflow = ? AND step = ? AND length(text) > 150",
            (scope["workflow"], scope["step"]),
        )
        total = db.query(
            "SELECT count(*) AS n FROM outputs WHERE workflow = ? AND step = ?",
            (scope["workflow"], scope["step"]),
        )[0]["n"]
        if len(rows) >= 3:
            found.append(
                tl.finding(
                    f"{len(rows)} of {total} ideas are longer than 150 characters",
                    category="quality",
                    scope=scope,
                    affected=rows,
                    total=total,
                    severity="low",
                    metric={"name": "long_idea_rate", "value": len(rows) / total, "baseline": 0.0},
                )
            )
    return found


runs = synthetic_runs("custom", n_runs=200)
ws = tl.Workspace("custom/lens")
ws.ingest(f"hone:{runs.root}")
report = ws.analyze("song_ideas", stages=("stats",))
mine = [f for f in report.findings if f.detector == "ideas_too_long"]
print(mine[0].id, mine[0].title)
assert mine and mine[0].title in ws.report("song_ideas")
```

What the registry does for you:

- The finding gets `detector` = the function name (or `name=` given to `@tl.detector`) and `category`
  (unless the finding sets its own). Registering a second detector with the same name raises
  `HoneLensError`.
- The workspace gives it an id, ranks it, keeps its status across analyses and shows it in every report.
- Inside `analyze(workflow, since=...)`, the tables `calls`, `steps`, `selections`, `outputs`,
  `run_calls` and `flow_steps` hold only the rows of that workflow and time range (temporary tables shadow
  the full ones), so plain SQL sees the same slice the built-in detectors see.

**Use `tl.finding(...)`** to build findings from the affected rows (dicts with `span_id`
and `start_time`, as in the example). It counts them, samples the evidence, records the time range in
`scope["time_range"]` (needed to re-check `fixed` findings) and keeps every affected span id, which
`explain` needs to rank causes. A `tl.Finding(...)` built by hand works in reports, but set
`affected_ids` yourself if you want `explain` to work on it. `hone_lens.findings.rate_finding(...)`
builds the "N% of <scope> <what>" findings the built-ins use.

The content key (and so the id) comes from the detector name, category, scope without the time range, and
metric name: keep them stable for the same issue.

## Analytics tables

`db.query(sql, params)` runs a read query and returns rows as dicts. Every derived table has these
context columns:

| Column | Meaning |
|---|---|
| `span_id` | the span this row comes from (primary key) |
| `trace_id` | its trace |
| `run_id` | `hone.run_id`, else the trace id |
| `workflow` | `hone.flow.workflow`, else the resource `service.name` |
| `step` | `hone.step`, else the parent span's name |
| `item` | `hone.item` |
| `start_time` | ISO-8601 UTC, e.g. `2026-09-27T14:03:11.120Z` (compares correctly as text) |

The other columns per table:

**`calls`** (one row per model call; replays have `replay_of` set):
`model`, `provider`, `scorer` (set on judge calls), `template_id`, `template_version`, `sections` (JSON
list of `{id, version, start, end, sha256}`), `structured_path` (`constrained`, `parsed`, `retried`,
`repaired`, `failed`), `retries` (count of `retry` events), `finish_reason`, `input_tokens`,
`output_tokens`, `context_limit`, `temperature`, `seed`, `input_sha` (hash of the prompt text),
`latency_ms`, `cost_usd`, `lease_wait_ms`, `vram_after_mb`, `gpu_unloaded`, `status` (`ok`, `error`,
`unset`), `error` (the status message of failed calls), `replay_of`.

**`steps`** (one row per `hone.flow.step` span): `step_version`, `source_hash`, `status`
(`hone.flow.status`: `done`, `failed`, `skipped`, `blocked`, `awaiting_review`, `interrupted`, `pending`),
`executed` (1 when the step ran and finished in this call: `done` / `failed` and not copied by a fork),
`attempt`, `reused_from`, `fork_of`, `params` (JSON object), `seed` (`hone.flow.seed`), `deterministic`, `duration_ms`,
`cpu_utilization`, `memory_peak_mb`, `gpu_peak_mb`, `gpu_utilization`, `error`.

**`selections`** (one row per `hone.select.*` or `hone.flow.gate` span): `kind` (`run`, `generate`,
`score`, `pairwise`, `gate`, `decision`, `human_gate`), `candidate_id`, `scorer`, `value`, `confidence`,
`reason`, `error`, `gate`, `passed`, `probability`, `decision`, `actor` and `actor_kind` (human gates; `automated` or `person`, `None` in older runs), `winner_id`,
`fallback_used`, `escalated`, `ranked` (JSON ids and totals), `status`.

**`outputs`** (one row per generator output: model calls with output text that are neither judge calls
nor replays): `step_span_id` (the enclosing `hone.flow.step` span, or the parent span), `model`,
`template_version`, `text`, `text_sha`.

**`run_calls`** (one row per `hone.flow.run` span, i.e. per run, resume or fork call on a run):
`status` (the run status at the end of the call), `fork_of`, `seed` (`hone.flow.seed`), `warnings` (JSON list of the attributes of
the span's `warning` events).

**`flow_steps`** (hone-flow step records from the `flow:` source; one row per run, step and item, no
`span_id` / `trace_id`): `run_id`, `workflow`, `step`, `item`, `kind` (`step`, `gate`, `global`), `status`,
`attempt`, `attempts` (earlier attempts), `attempt_statuses` (JSON: `failed`, `rejected`, `replaced`),
`reused_from`, `fork_of`, `run_seed` (the manifest's `seed`), `review_decisions` (JSON `decision`, `actor`, `actor_kind`, `at`, `attempt`),
`review_note_present`, `error`, `start_time`, `duration_ms`.

The workspace also has `spans` (every ingested span, records columns with JSON `attributes`), and
`clusters` (`id`, `workflow`, `step`, `size`, `total`, `center`, `members`, `description`, `in_common`).

```python
print(
    ws.db.query(
        "SELECT step, model, count(*) AS calls, round(avg(latency_ms)) AS ms FROM calls "
        "WHERE workflow = ? GROUP BY step, model ORDER BY calls DESC",
        ("song_ideas",),
    )
)
```
