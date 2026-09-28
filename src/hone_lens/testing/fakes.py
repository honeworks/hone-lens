"""Deterministic fakes for every port hone-lens owns. Public: use them in your own tests.

Every fake records its calls in `.calls` and passes the contract checkers in `contracts.py`.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hone_lens.mapping import output_text
from hone_lens.ports import TraceContext
from hone_lens.testing.analyst import answer
from hone_lens.testing.synthetic import varied_idea

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORD_TEXT = "a an the of and or to in on at as is are was be by for with from into it its this that"
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return [1.0] + [0.0] * (len(vector) - 1)
    return [x / norm for x in vector]


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


class FakeEmbedder:
    """Hash embeddings. Default: every distinct text gets an unrelated vector.

    `FakeEmbedder.semantic()` hashes words instead (bag of words), so texts that share words are
    similar, which is enough to exercise clustering and closeness::

        e = FakeEmbedder.semantic()
        a, b = e.embed(["a storm at sea", "storm at sea again"])
    """

    def __init__(self, dimensions: int = 64, *, by_words: bool = False) -> None:
        self.dimensions = dimensions
        self.by_words = by_words
        self.model_id = "fake-semantic" if by_words else "fake-hash"
        self.calls: list[list[str]] = []

    @classmethod
    def semantic(cls, dimensions: int = 256) -> FakeEmbedder:
        return cls(dimensions, by_words=True)

    def embed(self, texts: Sequence[str], *, trace: TraceContext | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        embed_one = self._by_words if self.by_words else self._by_text
        return [embed_one(t) for t in texts]

    def _by_text(self, text: str) -> list[float]:
        raw = b"".join(_digest(f"{i}:{text}") for i in range(math.ceil(self.dimensions / 32)))
        return _unit([b / 127.5 - 1.0 for b in raw[: self.dimensions]])

    def _by_words(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            if word not in _STOPWORDS:
                d = _digest(word)
                vector[int.from_bytes(d[:4], "big") % self.dimensions] += 1.0 if d[4] & 1 else -1.0
        return _unit(vector)


@dataclass
class FakeTextResult:
    """A `TextResult` (current.md §6) produced by `FakeTextClient`."""

    text: str
    parsed: Any = None
    error: str | None = None
    model: str = "fake-text"
    finish_reason: str | None = "stop"
    usage: dict[str, int] = field(default_factory=dict[str, int])
    span_id: str | None = None


Rule = Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any] | None], Any]


class FakeTextClient:
    """Scripted `TextClient`. Give a list of `responses` (used in order, the last repeats) or a `rule`
    `(messages, schema) -> str | dict`. Dict answers become `parsed` (and JSON `text`).

    `FakeTextClient.analyst()` answers hone-lens's own analysis prompts plausibly (see `analyst.py`).
    """

    def __init__(self, responses: Sequence[str | dict[str, Any]] = (), *, rule: Rule | None = None) -> None:
        if not responses and rule is None:
            responses = ["OK"]
        self.responses = list(responses)
        self.rule = rule
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def analyst(cls) -> FakeTextClient:
        return cls(rule=answer)

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: TraceContext | None = None,
        **params: Any,
    ) -> FakeTextResult:
        self.calls.append({"messages": list(messages), "schema": schema, "trace": trace, "params": params})
        reply = self._next(messages, schema)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        parsed: Any = None
        error: str | None = None
        if schema is not None:
            parsed = _try_json(text)
            error = None if parsed is not None else "response is not valid JSON for the schema"
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        usage = {"input_tokens": prompt_chars // 4 + 1, "output_tokens": len(text) // 4 + 1}
        return FakeTextResult(text=text, parsed=parsed, error=error, usage=usage)

    def _next(self, messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any] | None) -> Any:
        if self.rule is not None:
            return self.rule(messages, schema)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


ReplayRule = Callable[[Mapping[str, Any], Mapping[str, Any]], str]


class FakeReplayer:
    """Scripted `Replayer`. `rule(call_span, overrides) -> new output text`; the default returns the
    original output unchanged (so no change "helps", which is what a wrong cause looks like).

    `FakeReplayer.removing_section_reduces_similarity(section)` returns a fresh, varied output when that
    prompt section is removed or replaced, and the original output for any other change.
    """

    def __init__(self, rule: ReplayRule | None = None) -> None:
        self.rule: ReplayRule = rule or (lambda span, overrides: output_text(span))
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def removing_section_reduces_similarity(cls, section: str = "format_example") -> FakeReplayer:
        def rule(span: Mapping[str, Any], overrides: Mapping[str, Any]) -> str:
            if section not in overrides.get("prompt.sections", {}):
                return output_text(span)
            key = json.dumps([span["span_id"], overrides], sort_keys=True, default=str)
            return varied_idea(random.Random(_digest(key)))

        return cls(rule)

    def replay_call(
        self, call_span: Mapping[str, Any], overrides: Mapping[str, Any], *, trace: TraceContext | None = None
    ) -> dict[str, Any]:
        self.calls.append({"span": call_span, "overrides": overrides, "trace": trace})
        text = self.rule(call_span, overrides)
        attributes = dict(call_span.get("attributes", {}))
        attributes.update({k: v for k, v in (trace or {}).items() if k.startswith("hone.")})
        attributes["hone.models.replay_of"] = call_span["span_id"]
        attributes["gen_ai.output.messages"] = json.dumps([{"role": "assistant", "content": text}])
        attributes["gen_ai.response.finish_reasons"] = ["stop"]
        if "model" in overrides:
            attributes["gen_ai.request.model"] = attributes["gen_ai.response.model"] = overrides["model"]
        key = json.dumps([call_span["span_id"], overrides, len(self.calls)], sort_keys=True, default=str)
        trace_id, parent = _trace_ids(trace, call_span)
        new_id = _digest(key).hex()[:16]
        return {
            **call_span,
            "trace_id": trace_id,
            "parent_span_id": parent,
            "span_id": new_id,
            "attributes": attributes,
        }


def _trace_ids(trace: TraceContext | None, span: Mapping[str, Any]) -> tuple[str, str | None]:
    parts = (trace or {}).get("traceparent", "").split("-")
    if len(parts) == 4:
        return parts[1], parts[2]
    return str(span["trace_id"]), span.get("parent_span_id")


class FakeStepRerunner:
    """`StepRerunner` that returns one deterministic new run id per call (as a fork does) and records its
    calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str], dict[str, Any]]] = []

    def rerun(self, run_id: str, step: str, items: Sequence[str], params: Mapping[str, Any]) -> list[str]:
        self.calls.append((run_id, step, list(items), dict(params)))
        return [f"{run_id}-rerun-{len(self.calls)}"]


class FakeRecordSource:
    """`RecordSource` over an in-memory list of spans."""

    def __init__(self, spans: Iterable[Mapping[str, Any]], name: str = "fake") -> None:
        self.name = name
        self._spans = list(spans)

    def spans(self, *, since: str | None = None) -> list[Mapping[str, Any]]:
        return [s for s in self._spans if since is None or str(s["start_time"]) >= since]


class ScriptedIO:
    """A `ReviewIO` that answers from a list (then with each question's default) and records everything.

    `ScriptedIO(["", "better note", "y"])`: accept the first note, edit the second, confirm the third.
    """

    def __init__(self, answers: Iterable[str] = ()) -> None:
        self.answers = list(answers)
        self.shown: list[str] = []
        self.asked: list[str] = []

    def show(self, text: str) -> None:
        self.shown.append(text)

    def ask(self, question: str, default: str = "") -> str:
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else default
