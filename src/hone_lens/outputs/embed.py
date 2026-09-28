# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# (numpy's stubs leave some overloads partially unknown)
"""Embed texts through the `Embedder` port, cached in the workspace by (model, text hash)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from hone_lens.derive import text_sha
from hone_lens.errors import HoneLensError
from hone_lens.ports import Embedder
from hone_lens.store import AnalyticsDB

BATCH = 256


def embed(db: AnalyticsDB, embedder: Embedder, texts: Sequence[str]) -> NDArray[np.float32]:
    """One L2-normalized row per text (order kept). Texts embedded before with the same model are read from
    the cache, so re-analysis only embeds new outputs."""
    if not texts:
        return np.zeros((0, embedder.dimensions), dtype=np.float32)
    shas = [text_sha(t) for t in texts]
    cached = {
        sha: v for sha, v in db.vectors(embedder.model_id, set(shas)).items() if len(v) == embedder.dimensions
    }
    missing = list({sha: t for sha, t in zip(shas, texts, strict=True) if sha not in cached}.items())
    for start in range(0, len(missing), BATCH):
        batch = missing[start : start + BATCH]
        try:
            vectors = embedder.embed([t for _, t in batch])
        except Exception as e:
            raise HoneLensError(f"embedder {embedder.model_id!r} failed: {type(e).__name__}: {e}") from e
        if len(vectors) != len(batch):
            raise HoneLensError(
                f"embedder {embedder.model_id!r} returned {len(vectors)} vectors for {len(batch)} texts"
            )
        new = {sha: _unit(v) for (sha, _), v in zip(batch, vectors, strict=True)}
        db.save_vectors(embedder.model_id, new)
        cached.update(new)
    return np.stack([cached[sha] for sha in shas])


def _unit(vector: Sequence[float]) -> NDArray[np.float32]:
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm else v
