# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# (numpy's stubs leave some overloads partially unknown)
"""Deterministic density clustering of unit vectors (the built-in clustering; no extra dependencies).

Leader clustering on a seeded sample: the output with the most close neighbours (cosine >= `similarity`)
becomes a cluster centre and takes those neighbours; repeat while a centre still has `min_size` free
neighbours. Every output then joins its most similar centre, if close enough; the rest belong to no
cluster (label -1). Centres are real outputs, so a cluster is easy to show and describe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

SIMILARITY = 0.8
SAMPLE = 2000
MIN_SIZE = 5
MIN_SHARE = 0.02
MAX_CLUSTERS = 50
_CHUNK = 2048


@dataclass(frozen=True)
class Clustering:
    labels: NDArray[np.int64]  # cluster index per output, -1: in no cluster
    centers: list[int]  # output index of each cluster's centre, largest cluster first
    sample: NDArray[np.int64]  # outputs used for pairwise measures
    nearest: NDArray[np.float32]  # each output's similarity to its nearest sampled neighbour

    def sizes(self) -> list[int]:
        return [int(np.sum(self.labels == k)) for k in range(len(self.centers))]


def cluster(vectors: NDArray[np.float32], seed: int = 0, similarity: float = SIMILARITY) -> Clustering:
    """Cluster the rows of `vectors` (L2-normalized)."""
    n = len(vectors)
    if n == 0:
        return Clustering(
            np.zeros(0, dtype=np.int64), [], np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
        )
    rng = np.random.default_rng(seed)
    sample = np.sort(rng.choice(n, SAMPLE, replace=False)) if n > SAMPLE else np.arange(n)
    close = (vectors[sample] @ vectors[sample].T) >= similarity
    free = np.ones(len(sample), dtype=bool)
    min_size = max(MIN_SIZE, math.ceil(MIN_SHARE * len(sample)))
    leaders: list[int] = []
    while len(leaders) < MAX_CLUSTERS:
        counts = (close & free).sum(axis=1) * free
        best = int(np.argmax(counts))
        if counts[best] < min_size:
            break
        leaders.append(int(sample[best]))
        free &= ~close[best]
    labels, nearest = _assign(vectors, leaders, sample, similarity)
    order = sorted(range(len(leaders)), key=lambda k: -int(np.sum(labels == k)))  # largest first
    relabel = np.full(len(leaders) + 1, -1, dtype=np.int64)
    relabel[order] = np.arange(len(leaders))
    return Clustering(relabel[labels], [leaders[k] for k in order], sample.astype(np.int64), nearest)


def _assign(
    vectors: NDArray[np.float32], leaders: list[int], sample: NDArray[np.int64], similarity: float
) -> tuple[NDArray[np.int64], NDArray[np.float32]]:
    labels = np.full(len(vectors), -1, dtype=np.int64)
    nearest = np.zeros(len(vectors), dtype=np.float32)
    in_sample = np.zeros(len(vectors), dtype=bool)
    in_sample[sample] = True
    for start in range(0, len(vectors), _CHUNK):
        rows = np.arange(start, min(start + _CHUNK, len(vectors)))
        if leaders:
            to_leader = vectors[rows] @ vectors[leaders].T
            best = to_leader.argmax(axis=1)
            labels[rows] = np.where(to_leader.max(axis=1) >= similarity, best, -1)
        to_sample = vectors[rows] @ vectors[sample].T
        own = rows[in_sample[rows]]
        to_sample[own - start, np.searchsorted(sample, own)] = -1.0  # an output is not its own neighbour
        nearest[rows] = to_sample.max(axis=1) if len(sample) > 1 else 0.0
    return labels, nearest


def mean_pairwise(vectors: NDArray[np.float32], sample: NDArray[np.int64]) -> float | None:
    """Mean cosine similarity between different sampled outputs (the usual mode-collapse measure)."""
    m = len(sample)
    if m < 2:
        return None
    sims = vectors[sample] @ vectors[sample].T
    return float((sims.sum() - np.trace(sims)) / (m * (m - 1)))
