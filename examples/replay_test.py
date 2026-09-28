"""Stage 6: replay affected calls with the suspected cause changed, and confirm or refute it.

What: a counterfactual replay test: recorded model calls are sent again with one prompt section removed
or rewritten (everything else fixed), the finding's metric is measured on the replies, and the cause is
**confirmed** (the metric drops beyond noise and quality holds), **refuted** (no variant helps) or stays
suspected.

How: give the workspace a `Replayer` (design/current.md §6; hone-models provides one) and call
`ws.test(finding_id, variants=, samples=, budget=, quality=)`. It samples calls that have the suspected
section, builds variants (`remove`, LLM `rewrite`s when an LLM is set, `counter-instruction`) and calls
`replayer.replay_call(span, {"prompt.sections": {id: new_text_or_None}}, trace=...)` for each. The
`TestResult` has `metric_before` / `metric_after`, the winning `variant`, per-variant numbers, cost and
notes. The finding's own status never changes: a person decides.

Why: a correlation (stage 5) is not a cause. Replaying the same inputs with only the suspected section
changed is the cheapest experiment that tells them apart, and it measures the fix before anyone ships it.

Run: python examples/replay_test.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeReplayer, synthetic_runs

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=300, plant=["homogeneity_from_example"])
    # This fake replayer answers with varied ideas when the format_example section is removed or changed:
    # what a real model does when the example it copied is gone.
    replayer = FakeReplayer.removing_section_reduces_similarity()
    ws = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic(), replayer=replayer)
    ws.ingest(f"hone:{runs.root}")
    (homogeneity,) = ws.analyze("song_ideas", stages=("outputs",)).findings

    # 1. The right cause is confirmed. `test` runs `explain` first when the finding has no cause yet.
    #    `quality` is optional: a scorer text -> 0..1 that must not drop (here a toy length score).
    result = ws.test(homogeneity.id, variants=2, samples=20, quality=lambda text: min(len(text) / 100, 1.0))
    print(f"{homogeneity.id} {result.metric_name}: {result.metric_before} -> {result.metric_after}")
    print(f"  best variant {result.variant!r}, confirmed: {result.confirmed}, {result.calls} replayed calls")
    print(f"  quality {result.quality_before} -> {result.quality_after}")
    print("  variants:", [(v["variant"], v["metric_after"]) for v in result.variants])
    print("  first replay overrides:", replayer.calls[0]["overrides"])
    assert result.confirmed and result.metric_after is not None and result.metric_before is not None
    assert result.metric_after < result.metric_before
    # the cause is confirmed; the finding's own status is still a person's call
    tested = ws.finding(homogeneity.id)
    assert tested.cause is not None and tested.cause.status == "confirmed" and tested.status == "new"
    print("  fix:", tested.fix.description if tested.fix else None)

    # 2. A cause that does not matter is refuted. Same workspace folder, now with a replayer whose output
    #    never changes, whatever the prompt: what a section that does not matter looks like.
    unmoved = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic(), replayer=FakeReplayer())
    unmoved_result = unmoved.test(homogeneity.id, variants=1, samples=10)
    refuted = unmoved.finding(homogeneity.id)
    assert not unmoved_result.confirmed and refuted.cause is not None and refuted.cause.status == "refuted"
    print(f"\nwith an unmoved replayer: confirmed={unmoved_result.confirmed}, cause {refuted.cause.status}")
