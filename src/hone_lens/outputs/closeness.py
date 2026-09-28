"""Closeness of output clusters to the recorded prompt sections: which section do the outputs copy?"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from hone_lens.mapping import as_dict, as_list, json_value, messages_text
from hone_lens.outputs.embed import embed
from hone_lens.ports import Embedder
from hone_lens.store import AnalyticsDB

NO_SECTIONS = "prompt sections were not recorded for these outputs, so a section-level cause is unavailable"


def section_texts(db: AnalyticsDB, span_ids: Sequence[str]) -> dict[tuple[str, str], str]:
    """(section id, version) -> the section's text, cut from the first of these calls' prompts that has it."""
    wanted = set(span_ids)
    texts: dict[tuple[str, str], str] = {}
    for row in db.query("SELECT span_id, sections FROM calls WHERE sections IS NOT NULL ORDER BY start_time"):
        if row["span_id"] not in wanted:
            continue
        for s in map(as_dict, as_list(json.loads(row["sections"]))):
            key = (str(s.get("id")), str(s.get("version")))
            if key not in texts and isinstance(s.get("start"), int) and isinstance(s.get("end"), int):
                texts[key] = _prompt(db, row["span_id"])[s["start"] : s["end"]]
    return {k: t for k, t in texts.items() if t.strip()}


def _prompt(db: AnalyticsDB, span_id: str) -> str:
    (row,) = db.query("SELECT attributes FROM spans WHERE span_id = ?", (span_id,))
    return messages_text(json_value(json.loads(row["attributes"]).get("gen_ai.input.messages")))


def closeness(
    db: AnalyticsDB,
    embedder: Embedder,
    sections: Mapping[tuple[str, str], str],
    vectors: NDArray[np.float32],
    members: NDArray[np.bool_],
) -> list[dict[str, Any]]:
    """For each prompt section: mean similarity of the cluster's outputs (`members`) to the section's text,
    of the other outputs, and the difference; the most copied section first. `[]` when there is nothing to
    compare (no sections, or no outputs inside / outside the cluster)."""
    if not sections or members.all() or not members.any():
        return []
    keys = sorted(sections)
    sims = vectors @ embed(db, embedder, [sections[k] for k in keys]).T
    rows: list[dict[str, Any]] = []
    for j, (section, version) in enumerate(keys):
        near, rest = float(sims[members, j].mean()), float(sims[~members, j].mean())
        rows.append(
            {
                "section": section,
                "version": version,
                "cluster": near,
                "others": rest,
                "difference": near - rest,
            }
        )
    return sorted(rows, key=lambda r: -r["difference"])
