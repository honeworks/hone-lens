"""The scripted analyst behind `FakeTextClient.analyst()`.

hone-lens's analysis prompts send a JSON payload as the last user message and a JSON Schema whose
`title` names the task. This module answers each task with simple, deterministic text heuristics, good
enough to drive the whole funnel in tests without a real model.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_WORD = re.compile(r"[a-z]+")
_COMMON_TEXT = (
    "a an the of and or to in on at as is are was be by for with from into it its this that who song"
)
_COMMON = frozenset(_COMMON_TEXT.split())


def _word_list(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _COMMON and len(w) > 2]


def _words(text: str) -> set[str]:
    return set(_word_list(text))


def shared_words(texts: Sequence[str], share: float = 0.6) -> list[str]:
    """Words that appear in at least `share` of the texts, most frequent first (ties alphabetical)."""
    counts = Counter(w for t in texts for w in _words(t))
    need = max(2, math.ceil(share * len(texts)))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, c in ranked if c >= need][:12]


def _describe_cluster(p: Mapping[str, Any]) -> dict[str, Any]:
    items = [str(i) for i in p.get("items", [])]
    common = shared_words(items)
    if not common:
        return {"description": f"{len(items)} varied outputs", "in_common": "nothing specific"}
    return {"description": f"Outputs built around: {', '.join(common)}", "in_common": ", ".join(common)}


def _note(p: Mapping[str, Any]) -> dict[str, Any]:
    text = str(p.get("output", ""))
    words = _words(text)
    if {"storm", "captain"} <= words:
        return {"note": "copies the storm and captain structure of the format example"}
    if text and not text.rstrip().endswith((".", "!", "?")):
        return {"note": "output is cut off mid-sentence"}
    return {"note": "no obvious problem"}


def _taxonomy(p: Mapping[str, Any]) -> dict[str, Any]:
    notes = [str(n) for n in p.get("notes", [])]
    modes: list[dict[str, str]] = []
    for note, _ in Counter(notes).most_common(8):
        if note == "no obvious problem":
            continue
        name = "_".join(list(dict.fromkeys(_word_list(note)))[:3]) or "other"
        modes.append({"name": name, "description": note})
    return {"modes": modes}


def _label(p: Mapping[str, Any]) -> dict[str, Any]:
    note = _note({"output": p.get("output", "")})["note"]
    for mode in p.get("modes", []):
        if str(mode.get("description", "")) == note:
            return {"mode": mode.get("name")}
    return {"mode": None}


def _hypothesis(p: Mapping[str, Any]) -> dict[str, Any]:
    causes: list[Mapping[str, Any]] = p.get("causes") or []
    if not causes:
        return {"hypothesis": "No recorded input explains this finding."}
    top = causes[0]
    return {
        "hypothesis": f"Affected runs share {top.get('kind')} {top.get('target')!r}; "
        f"outputs likely follow it (effect {float(top.get('effect') or 0):.2f})."
    }


def _variants(p: Mapping[str, Any]) -> dict[str, Any]:
    n = int(p.get("n", 1))
    samples = (
        "Example: a baker counts the days in a rainy suburb.",
        "Example: two old friends chase a parade through a mountain town.",
        "Example: a lonely robot plants tomatoes on a rooftop garden.",
    )
    return {"variants": ["\n".join(samples[: 1 + i % 3]) for i in range(n)]}


TASKS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "cluster_description": _describe_cluster,
    "trace_note": _note,
    "taxonomy": _taxonomy,
    "label": _label,
    "hypothesis": _hypothesis,
    "section_variants": _variants,
}


def answer(messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any] | None) -> Any:
    """Answer one analysis prompt; plain prompts without a known task get 'OK'."""
    task = (schema or {}).get("title")
    if task not in TASKS:
        return {"ok": True} if schema is not None else "OK"
    try:
        payload = json.loads(str(messages[-1].get("content", "{}")))
    except ValueError:
        return f"cannot read the {task} payload: the last message must be JSON"
    return TASKS[task](payload)
