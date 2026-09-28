"""Synthetic runs with planted issues: the test bed for every detector and acceptance case.

`synthetic_runs(root, n_runs, plant=[...])` writes honeworks span stores (`runs/{models,select}/spans.db`),
hone-flow run folders (`runs/flows/song_ideas/runs/<run_id>/spans.jsonl` + a minimal `manifest.json`) and
the same generator calls as plain OpenTelemetry GenAI traces (`runs-otlp/traces-<seed>.json`, no `hone.*`
fields). `SyntheticRuns.truth` says what each plant should produce. Everything derives from `seed`: the
same seed rewrites the same runs; another seed appends new, later runs.
"""

from __future__ import annotations

import copy
import hashlib
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hone_lens.testing._otlp_writer import write_otlp
from hone_lens.testing._store_writer import write_run_folder, write_store
from hone_lens.testing._synthetic_spans import (
    GENERATOR,
    JUDGE_SAME_FAMILY,
    STEP,
    WORKFLOW,
    RunPlan,
    run_spans,
)

FORMAT_EXAMPLE = (
    "Example: A storm opens the song as Captain Mara steers the Aurora through black water. "
    "Three-line intro: waves, thunder, a lantern swinging."
)
ROLE = (
    "You are a songwriter who pitches fresh, specific song ideas for a small studio. Every idea names a "
    "concrete character, a place and one striking image. Avoid cliches, avoid rhyming the pitch itself, and "
    "keep the tone warm. Ideas are read by a producer who picks one per day, so each must stand on its own "
    "and be different from yesterday's. Write plain sentences without lists, headings or quotation marks."
)
PLANTS = (
    "homogeneity_from_example",
    "truncation",
    "judge_equals_generator",
    "score_zero_spike",
    "vision_400",
    "gpu_thrash",
    "source_changed_version_unchanged",
    "nondeterministic_seed",
    "latency_regression_after_v4",
)
_W, _S = {"workflow": WORKFLOW}, {"workflow": WORKFLOW, "step": STEP}
# detector / metric: which built-in detector must find it, with which metric; also: detectors that may
# legitimately fire too; rate: roughly the affected share the plant creates.
TRUTH: dict[str, dict[str, Any]] = {
    "homogeneity_from_example": {
        "metric": "largest_cluster_share",
        "detector": "homogeneity",
        "category": "diversity",
        "scope": _S,
        "rate": 0.4,
        "cause": ("prompt_section", "format_example"),
        "also": (),
    },
    "truncation": {
        "metric": "truncation_rate",
        "detector": "truncation",
        "category": "reliability",
        "rate": 0.05,
        "scope": {**_S, "model": GENERATOR},
        "also": ("failure_rate",),
    },
    "judge_equals_generator": {
        "metric": "same_family_judge_share",
        "detector": "setup_smells",
        "category": "setup",
        "rate": 1.0,
        "scope": {**_W, "scorer": "quality", "model": JUDGE_SAME_FAMILY},
        "also": (),
    },
    "score_zero_spike": {
        "metric": "zero_score_rate",
        "detector": "selection_health",
        "category": "quality",
        "rate": 0.1,
        "scope": {**_W, "scorer": "quality"},
        "also": ("failure_rate",),
    },
    "vision_400": {
        "metric": "capability_error_rate",
        "detector": "setup_smells",
        "category": "setup",
        "rate": 1.0,
        "scope": {**_W, "scorer": "cover_art", "model": GENERATOR},
        "also": ("failure_rate",),
    },
    "gpu_thrash": {
        "metric": "model_swaps_per_run",
        "detector": "gpu_thrash",
        "category": "cost",
        "rate": 1.0,
        "scope": _W,
        "also": ("failure_rate",),
    },
    "source_changed_version_unchanged": {
        "metric": "stale_source_rate",
        "detector": "reuse_health",
        "category": "reliability",
        "rate": 0.15,
        "scope": _S,
        "also": (),
    },
    "nondeterministic_seed": {
        "metric": "unseeded_repeat_rate",
        "detector": "setup_smells",
        "category": "setup",
        "rate": 0.3,
        "scope": _S,
        "also": (),
    },
    "latency_regression_after_v4": {
        "metric": "latency_ms_median",
        "detector": "regression",
        "category": "regression",
        "rate": 0.25,
        "scope": {**_S, "prompt_version": "4"},
        "also": (),
    },
}

_NAMES = ("Mara", "Ilse", "Tomas", "Rook", "Anya", "Bram", "Cole", "Duna")
_SHIPS = ("Aurora", "Kestrel", "Marlin", "Osprey")
_POOLS = {
    "opening": (
        "A quiet verse",
        "A shouted chorus",
        "A spoken intro",
        "A slow piano line",
        "A handclap beat",
        "A whispered hook",
        "A banjo riff",
        "A drum fill",
        "A choir swell",
        "A radio crackle",
    ),
    "subject": (
        "a baker",
        "two old friends",
        "a night-shift nurse",
        "a lost dog",
        "a city bus driver",
        "a retired boxer",
        "a teenage astronomer",
        "a street painter",
        "a lighthouse keeper's cat",
        "a wedding band",
        "a beekeeper",
        "a subway busker",
        "a chess hustler",
        "a lonely robot",
    ),
    "action": (
        "counts the days",
        "fixes a broken clock",
        "learns to dance",
        "writes letters never sent",
        "plants tomatoes",
        "chases a parade",
        "sells lemonade",
        "paints the kitchen yellow",
        "misses the last train",
        "finds an old photograph",
        "trains for a marathon",
    ),
    "place": (
        "a rainy suburb",
        "a desert motel",
        "a rooftop garden",
        "a crowded laundromat",
        "a mountain town",
        "a closing diner",
        "an empty stadium",
        "a ferry deck",
        "a snowed-in cabin",
        "a flea market",
    ),
    "image": (
        "burnt toast",
        "neon reflections",
        "a cracked phone screen",
        "paper lanterns",
        "orange peel",
        "chalk drawings",
        "a borrowed jacket",
        "a flickering porch light",
        "sunflower seeds",
    ),
    "ending": (
        "a slammed door",
        "a phone call",
        "laughter",
        "a long hug",
        "silence",
        "a sunrise",
        "a key change",
        "a final bow",
        "rain on the windows",
    ),
}


def varied_idea(rng: random.Random) -> str:
    """A song idea drawn from a large combinatorial space (outputs that do not share a pattern)."""
    p = {k: rng.choice(v) for k, v in _POOLS.items()}
    return (
        f"{p['opening']} about {p['subject']} who {p['action']} in {p['place']}; "
        f"the chorus turns on {p['image']} and ends with {p['ending']}."
    )


def pattern_idea(rng: random.Random) -> str:
    """A song idea that copies the structure of FORMAT_EXAMPLE (storm, named captain, three-line intro)."""
    return (
        f"A storm opens the song as Captain {rng.choice(_NAMES)} steers the {rng.choice(_SHIPS)} "
        "through black water. Three-line intro: waves, thunder, a lantern swinging."
    )


@dataclass(frozen=True)
class SyntheticRuns:
    """Where the synthetic stores were written and what the plants should produce.

    `root` holds everything `hone:` reads; `flows` is the hone-flow storage root inside it (run folders
    under `flows/song_ideas/runs/`); `otlp_path` is the plain-OTel file.
    """

    root: Path
    flows: Path
    otlp_path: Path
    n_runs: int
    plants: tuple[str, ...]
    truth: dict[str, dict[str, Any]]


def synthetic_runs(
    root: str | Path, n_runs: int = 2000, plant: Sequence[str] = (), seed: int = 0
) -> SyntheticRuns:
    """Write `n_runs` runs of the `song_ideas` workflow with the given planted issues.

    >>> runs = synthetic_runs(tmp_path, n_runs=50, plant=["truncation"])  # doctest: +SKIP
    >>> runs.truth["truncation"]["detector"]  # doctest: +SKIP
    'truncation'
    """
    unknown = sorted(set(plant) - set(PLANTS))
    if unknown:
        raise ValueError(f"unknown plant(s) {unknown}; choose from {list(PLANTS)}")
    if n_runs < 0:
        raise ValueError(f"n_runs must be >= 0, got {n_runs}")
    plants = tuple(dict.fromkeys(plant))
    root = Path(root)
    flows = root / "runs" / "flows"
    spans: dict[str, list[dict[str, Any]]] = {"models": [], "select": [], "otlp": []}
    for i in range(n_runs):
        one_run = _run(seed, i, n_runs, plants)
        flow = one_run.pop("flow")
        write_run_folder(flows / WORKFLOW / "runs" / flow[0]["attributes"]["hone.run_id"], flow)
        for key, key_spans in one_run.items():
            spans[key].extend(key_spans)
    otlp_path = root / "runs-otlp" / f"traces-{seed}.json"
    write_otlp(otlp_path, spans.pop("otlp"), service=WORKFLOW)
    for package, package_spans in spans.items():
        write_store(root / "runs" / package / "spans.db", package_spans, package=f"hone-{package}")
    truth = {p: copy.deepcopy(TRUTH[p]) for p in plants}
    return SyntheticRuns(root / "runs", flows, otlp_path, n_runs, plants, truth)


def _run(seed: int, i: int, n_runs: int, plants: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    # Each call's runs go v2 (first half) then v3; appended runs (another seed) start at v2 again.
    position = i / max(n_runs, 1)
    version = "2" if position < 0.5 else "3"
    if "latency_regression_after_v4" in plants and position >= 0.75:
        version = "4"
    start = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=20 * seed, seconds=30 * i)
    plan = RunPlan(seed, i, random.Random(f"{seed}:{i}"), plants, version, start)
    prompt, sections = _prompt(plan)
    return run_spans(plan, prompt, sections, _output(plan))


def _prompt(plan: RunPlan) -> tuple[str, list[dict[str, Any]]]:
    theme = f"theme {plan.seed}-{plan.index}"
    if "nondeterministic_seed" in plan.plants and plan.rng.random() < 0.3:
        theme = f"shared theme {plan.rng.randrange(20)}"
    task_version = "4" if plan.version == "4" else "2"
    parts = [
        ("role", "1", ROLE),
        ("task", task_version, f"Pitch one song idea about {theme} in two sentences."),
    ]
    if plan.version in ("3", "4"):
        parts.append(("format_example", "3", FORMAT_EXAMPLE))
    prompt, sections = "", list[dict[str, Any]]()
    for section_id, version, text in parts:
        start = len(prompt) + (2 if prompt else 0)
        prompt = f"{prompt}\n\n{text}" if prompt else text
        sha = hashlib.sha256(text.encode()).hexdigest()
        sections.append(
            {"id": section_id, "version": version, "start": start, "end": len(prompt), "sha256": sha}
        )
    return prompt, sections


def _output(plan: RunPlan) -> str:
    # 80% of the v3/v4 runs (half of all runs) copy the example: ~40% of all outputs, as in current.md §1.
    copies = "homogeneity_from_example" in plan.plants and plan.version != "2" and plan.rng.random() < 0.8
    return pattern_idea(plan.rng) if copies else varied_idea(plan.rng)
