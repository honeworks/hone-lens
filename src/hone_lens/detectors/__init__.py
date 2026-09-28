"""Stage 1: statistics detectors over the analytics tables (no LLM), and the `@detector` registry.

A detector is a plain function `(db: AnalyticsDB) -> list[Finding]`. The built-in ones are listed in
`DETECTORS`; `@detector` adds your own::

    @tl.detector(category="quality")
    def hooks_too_long(db: tl.AnalyticsDB) -> list[tl.Finding]:
        rows = db.query("SELECT span_id, text FROM outputs WHERE length(text) > 400")
        return [tl.Finding(title=f"{len(rows)} hooks too long", affected=len(rows), evidence=[...])]
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hone_lens.detectors.changes import regression
from hone_lens.detectors.reliability import failure_rate, reuse_health, truncation
from hone_lens.detectors.resources import cost_latency, gpu_thrash
from hone_lens.detectors.review import human_override
from hone_lens.detectors.selection import selection_health
from hone_lens.detectors.setup import setup_smells
from hone_lens.errors import HoneLensError
from hone_lens.findings import Finding
from hone_lens.store import AnalyticsDB

DetectorFn = Callable[[AnalyticsDB], list[Finding]]


@dataclass(frozen=True)
class Detector:
    name: str
    category: str
    fn: DetectorFn


DETECTORS: dict[str, Detector] = {
    d.name: d
    for d in (
        Detector("failure_rate", "reliability", failure_rate),
        Detector("truncation", "reliability", truncation),
        Detector("cost_latency", "cost", cost_latency),
        Detector("gpu_thrash", "cost", gpu_thrash),
        Detector("reuse_health", "reliability", reuse_health),
        Detector("selection_health", "quality", selection_health),
        Detector("human_override", "quality", human_override),
        Detector("setup_smells", "setup", setup_smells),
        Detector("regression", "regression", regression),
    )
}


def detector(category: str = "quality", name: str | None = None) -> Callable[[DetectorFn], DetectorFn]:
    """Register a custom detector; `analyze` runs it after the built-in ones. The findings it returns get
    `category` (unless they set their own) and the detector's name (default: the function name). Build
    findings with `scope["time_range"]` (as `findings.finding()` does) so fixed ones are re-checked."""

    def register(fn: DetectorFn) -> DetectorFn:
        key = name or fn.__name__
        if key in DETECTORS:
            raise HoneLensError(f"a detector named {key!r} is already registered; pass name=...")
        DETECTORS[key] = Detector(key, category, fn)
        return fn

    return register


def run_detectors(db: AnalyticsDB, notes: list[str]) -> list[Finding]:
    """Every registered detector's findings. A detector that raises is reported in `notes` and skipped."""
    findings: list[Finding] = []
    for d in DETECTORS.values():
        try:
            found = d.fn(db)
        except Exception as e:
            notes.append(f"detector {d.name} failed: {type(e).__name__}: {e}")
            continue
        for f in found:
            f.detector = f.detector or d.name
            f.category = f.category or d.category
        findings += found
    return findings
