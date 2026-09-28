# 0004: Zero scores on a discrete scale are not "failures recorded as 0"

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28): options 2 and 3 as decided. Thresholds are in
[decisions.md](../decisions.md#stage-1-detectors).

## Context

Found while building the OneShotStudio demo app. Its keyframe picks use two hone-select
`PromptScorer`s (`faithful`, `clean`) on a 1-5 scale, answered by `qwen2.5vl-7b`; hone-select maps the
raw answer to `(raw - 1) / 4`, so the only possible values are 0, 0.25, 0.5, 0.75 and 1. hone-lens'
stats stage over the three demo runs reported:

```text
F-0001  [high] quality  46% of oneshot (scorer clean) scores are exactly 0 (failed scores recorded as 0?)
F-0003  [high] quality  20% of oneshot (scorer faithful) scores are exactly 0 (failed scores recorded as 0?)
```

The zeros were real answers, not failures: each `hone.select.score` span has confidence 0.9, an empty
error and a reason ("The image is a painting ... which is not a clean, well-composed picture"). The
finding was still valuable, because it exposed a criterion the judge misread (it penalised paintings as
"not clean"; the app reworded the criterion), but its title pointed at the wrong cause.

`_zero_spike` (`detectors/selection.py`) calls a spike of zeros "failures" when there are few values in
`(0, 0.2)` next to them. On a 5-point scale mapped to 0-1 no value can fall in `(0, 0.2)`, so every
scale-based judge that sometimes answers "1" looks like a broken scorer.

## Problem

- The rule assumes continuous scores. Discrete scales (hone-select prompt scorers, hone-taste panels on
  1-5, any `ScoreQ`) make it misfire, with `high` severity.
- The spans already say whether a zero was a real answer (a reason, a confidence, no error), and the rule
  ignores that.

## Options

1. Keep the rule; users dismiss the finding.
2. **Use the scale**: when a scorer's values take few distinct levels, compare zeros with the next level
   up (0.25 here) instead of `(0, 0.2)`.
3. **Use the evidence**: count a zero as "possibly failed" only when it has no reason, no confidence, or
   an error-like reason; otherwise report a different finding: "N% of <scorer> scores are the lowest
   possible (the judge rejects these outputs, or misreads the criterion)", category `quality`, severity
   by rate, with sample reasons as evidence.

## Decision

Proposed: 2 and 3 together. The misfire disappears, and the real signal (a judge that gives the floor
value often) is kept under an accurate title with the reasons that explain it, which is what led to the
fix in OneShotStudio.

## Consequences

- Fewer false "failed scores" findings for prompt scorers and panels.
- A new finding kind ("floor scores") that points at criteria wording or genuinely bad outputs.
- The detector reads `hone.select.score.reason` / `confidence` (already ingested as span attributes).

## Migration and compatibility

No API change. Existing `zero_score_rate` findings on discrete-scale scorers stop reproducing and are
closed like any resolved finding; new `floor_score_rate` findings appear instead.
