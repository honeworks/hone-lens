# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# (numpy's stubs leave some overloads partially unknown)
"""Homogeneity measures of one step's outputs and the findings they raise."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import numpy as np

from hone_lens.findings import Finding, finding, pct, severity_for, where
from hone_lens.outputs.cluster import Clustering, mean_pairwise
from hone_lens.stats import Row, group_by, two_proportion_p

LARGEST_SHARE = 0.2  # a cluster holding this share of a step's outputs is a finding
DISTINCT_RATE = 0.5  # fewer distinct outputs than this share is a finding
REGRESSION = 0.1  # ... and so is a largest-cluster share 10 points up from the first prompt version
ALPHA = 0.01
OUTLIER_SPREAD = 6.0
_WORD = re.compile(r"\w+")


def measures(rows: list[Row], vectors: np.ndarray[Any, Any], c: Clustering) -> dict[str, Any]:
    """Mean pairwise cosine, largest-cluster share, distinct-output rate and repeated 4-gram rate."""
    sizes = c.sizes()
    return {
        "outputs": len(rows),
        "mean_pairwise_cosine": mean_pairwise(vectors, c.sample),
        "largest_cluster_share": sizes[0] / len(rows) if sizes else 0.0,
        "distinct_rate": len({r["text_sha"] for r in rows}) / len(rows),
        "repeated_ngram_rate": _repeated_ngram_rate([r["text"] for r in rows]),
        "clusters": len(sizes),
    }


def _repeated_ngram_rate(texts: list[str], n: int = 4) -> float:
    """Share of word n-grams (counted once per output) that also occur in another output."""
    counts: Counter[tuple[str, ...]] = Counter()
    for text in texts:
        words = _WORD.findall(text.lower())
        counts.update({tuple(words[i : i + n]) for i in range(len(words) - n + 1)})
    total = sum(counts.values())
    return sum(c for c in counts.values() if c > 1) / total if total else 0.0


def homogeneity(
    scope: dict[str, Any], rows: list[Row], m: dict[str, Any], c: Clustering, details: dict[str, Any]
) -> list[Finding]:
    """A diversity finding when one cluster holds too many outputs or too few outputs are distinct."""
    findings: list[Finding] = []
    share = m["largest_cluster_share"]
    counts = _by_version(rows, c)
    by_version = {version: top / n for version, (top, n) in counts.items()}
    if share >= LARGEST_SHARE or _regressed(counts):
        members = [r for r, label in zip(rows, c.labels, strict=True) if label == 0]
        f = finding(
            f"{pct(share)} of {where(scope)} outputs share one pattern",
            category="diversity",
            scope=scope,
            affected=members,
            total=len(rows),
            detector="homogeneity",
            severity=severity_for(share, high=0.3, medium=LARGEST_SHARE),
            metric={"name": "largest_cluster_share", "value": share, "baseline": _baseline(by_version)},
            details={**details, "measures": m, "by_prompt_version": by_version},
        )
        if details.get("cluster_id"):
            f.evidence.insert(0, details["cluster_id"])  # the cluster first, then sample outputs
        findings.append(f)
    if m["distinct_rate"] < DISTINCT_RATE:
        dupes = [rows_[0] for rows_ in group_by(rows, "text_sha").values() if len(rows_) > 1]
        findings.append(
            finding(
                f"Only {pct(m['distinct_rate'])} of {where(scope)} outputs are distinct",
                category="diversity",
                scope=scope,
                affected=dupes,
                total=len(rows),
                detector="homogeneity",
                severity="high",
                metric={"name": "distinct_rate", "value": m["distinct_rate"], "baseline": 1.0},
                details={"measures": m},
            )
        )
    return findings


def _by_version(rows: list[Row], c: Clustering) -> dict[str, tuple[int, int]]:
    """Prompt version -> (outputs in the largest cluster, outputs), in first-seen order."""
    counts: dict[str, tuple[int, int]] = {}
    for r, label in zip(rows, c.labels, strict=True):
        if r["template_version"] is not None:
            top, n = counts.get(str(r["template_version"]), (0, 0))
            counts[str(r["template_version"])] = (top + int(label == 0), n + 1)
    return counts


def _regressed(counts: dict[str, tuple[int, int]]) -> bool:
    """Largest-cluster share up >= 10 points (significantly) from the first prompt version to the last."""
    if len(counts) < 2:
        return False
    (top_a, n_a), (top_b, n_b) = next(iter(counts.values())), list(counts.values())[-1]
    return top_b / n_b - top_a / n_a >= REGRESSION and two_proportion_p(top_a, n_a, top_b, n_b) < ALPHA


def _baseline(by_version: dict[str, float]) -> float | None:
    return min(by_version.values()) if len(by_version) > 1 else None


def outliers(scope: dict[str, Any], rows: list[Row], c: Clustering) -> list[Finding]:
    """Outputs far from every other output (often failures or garbage): nearest-neighbour similarity below
    median - 6 x (median - lower quartile), a robust lower fence."""
    if len(rows) < 20:
        return []
    median, quartile = np.percentile(c.nearest, [50, 25])
    fence = median - OUTLIER_SPREAD * max(float(median - quartile), 0.02)
    far = [r for r, near in zip(rows, c.nearest, strict=True) if near < fence]
    if len(far) < 3:
        return []
    return [
        finding(
            f"{len(far)} {where(scope)} outputs are far from every other output",
            category="quality",
            scope=scope,
            affected=far,
            total=len(rows),
            detector="outliers",
            severity="low",
            metric={"name": "outlier_rate", "value": len(far) / len(rows), "baseline": 0.0},
        )
    ]
