"""Bring your own models: implement the `TextClient`, `Embedder` and `Replayer` ports.

What: the small protocols hone-lens uses to talk to models (`TextClient`, `Embedder`, `Replayer`) and to
read records (`RecordSource`), each implemented in a few lines around a plain function, checked with the
contract checkers, and used for the whole funnel (analyze with descriptions, explain, replay test, ingest).

How: a port is a `typing.Protocol` (`hone_lens.ports`): any object with the right method works, with no
base class and no registration.
- `TextClient.complete(messages, *, schema=None, trace=None, **params)` returns `text`, `parsed` (the JSON
  answer when a `schema` is given), `error`, `usage`, ...
- `Embedder`: `model_id`, `dimensions`, `embed(texts, *, trace=None)` -> L2-normalized vectors.
- `Replayer.replay_call(call_span, overrides, *, trace=None)` re-runs a recorded call with
  `{"prompt.sections": {id: new_text_or_None}}` and returns the new call span (`hone.models.replay_of`).
- `RecordSource`: `name` and `spans(*, since=None)` yielding span dicts (design/current.md §7).
`check_text_client`, `check_embedder`, `check_replayer` and `check_record_source` (`hone_lens.testing`) raise
`AssertionError` with a message when an implementation breaks its contract. Replace the three toy
functions below with calls to your model API (OpenAI, Ollama, a local model); nothing else changes.
(hone-models implements all three; `hone_lens.adapters.openai` covers OpenAI-compatible servers.)

Why: hone-lens depends on no model provider. The ports keep it usable with whatever you already run, and
the contract checkers catch a broken adapter before it produces misleading findings.

Run: python examples/own_adapters.py
"""

import hashlib
import json
import math
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import (
    check_embedder,
    check_record_source,
    check_replayer,
    check_text_client,
    synthetic_runs,
)

# --- three toy "models": replace these with real calls -------------------------------------------------


def toy_chat(messages: list[dict], schema: dict | None) -> str:
    """Answers any task. With a schema: a JSON object with a value for every property it asks for."""
    if schema is None:
        return "OK"
    answer = {}
    for name, spec in schema["properties"].items():
        if spec.get("type") == "boolean":
            answer[name] = True
        elif spec.get("type") == "array":
            answer[name] = []
        else:
            answer[name] = f"({schema['title']}) the outputs repeat one storm-at-sea pattern"
    return json.dumps(answer)


def toy_embed(text: str, dimensions: int) -> list[float]:
    """A hashed bag of words, scaled to length 1 (the port wants L2-normalized vectors)."""
    vector = [0.0] * dimensions
    for word in text.lower().split():
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dimensions] += 1.0
    length = math.sqrt(sum(x * x for x in vector))
    if length == 0:  # an empty text still needs a unit vector
        return [1.0] + [0.0] * (dimensions - 1)
    return [x / length for x in vector]


def toy_generate(prompt: str) -> str:
    """Copies the storm example when the prompt shows it; otherwise a varied idea."""
    if "Captain" in prompt:
        return "A storm opens the song as Captain Mara steers the Aurora through black water."
    people = ("baker", "nurse", "boxer", "painter", "beekeeper", "busker", "astronomer", "robot")
    person = people[int(hashlib.sha256(prompt.encode()).hexdigest(), 16) % len(people)]  # stable per prompt
    return f"A quiet song about a {person} who learns to dance."


# --- the adapters -------------------------------------------------------------------------------------


class MyTextClient:
    """TextClient: chat messages (+ a JSON Schema) in; `text`, `parsed`, `error`, `usage`, ... out.

    A problem with the answer (not JSON, refused) goes into `error`; a failed transport (network, auth)
    may raise. hone-lens turns both into report notes and goes on.
    """

    model = "toy-chat-1"

    def complete(self, messages, *, schema=None, trace=None, **params):  # ignore params you do not know
        text = toy_chat(messages, schema)
        parsed, error = None, None
        if schema is not None:
            try:
                parsed = json.loads(text)
            except ValueError as e:
                error = f"the answer is not JSON ({e})"
        prompt_chars = sum(len(str(m["content"])) for m in messages)
        return {
            "text": text,
            "parsed": parsed,
            "error": error,
            "model": self.model,
            "finish_reason": "stop",
            "usage": {
                "input_tokens": prompt_chars // 4,
                "output_tokens": len(text) // 4,
            },  # budgets count these
            "span_id": None,  # the id of the span your client recorded for this call, if it records one
        }


class MyEmbedder:
    """Embedder: texts in, one L2-normalized vector of `dimensions` floats per text out, in order."""

    model_id = "word-hash-v1"  # keys the embedding cache in the workspace: change it when the vectors change
    dimensions = 128

    def embed(self, texts, *, trace=None):
        return [toy_embed(t, self.dimensions) for t in texts]


def as_json(value):
    """Span attributes holding lists or objects may be JSON strings (the OpenTelemetry encoding) or
    decoded values; a replayer accepts both (design/current.md §6)."""
    return json.loads(value) if isinstance(value, str) else value


def rebuild(prompt: str, sections: list[dict], replace: dict) -> str:
    """The prompt with sections replaced ({id: new text, or None to remove it}), last section first so the
    character offsets of the others stay valid."""
    for section in sorted(sections, key=lambda section: section["start"], reverse=True):
        if section["id"] in replace:
            prompt = prompt[: section["start"]] + (replace[section["id"]] or "") + prompt[section["end"] :]
    return prompt


class MyReplayer:
    """Replayer: a recorded call span + overrides in; the new call, recorded as a span, out."""

    def replay_call(self, call_span, overrides, *, trace=None):
        attributes = call_span["attributes"]
        messages = as_json(attributes["gen_ai.input.messages"])
        sections = as_json(attributes.get("hone.models.prompt.sections", "[]"))
        # the sections are offsets into the last message's text: rebuild it, keep the other messages
        last = messages[-1]
        prompt = rebuild(last["content"], sections, overrides.get("prompt.sections", {}))
        messages = [*messages[:-1], {**last, "content": prompt}]
        started = datetime.now(UTC)
        output = toy_generate(prompt)
        ended = datetime.now(UTC)

        # join the caller's trace (design/current.md §6): trace and parent ids from `traceparent`, and the
        # hone.* keys (hone.lens.finding_id) as attributes, so the replay can be traced back to its finding
        trace = trace or {}
        parts = trace.get("traceparent", "").split("-")
        trace_id, parent = (parts[1], parts[2]) if len(parts) == 4 else (call_span["trace_id"], None)
        new_attributes = {
            **attributes,
            **{k: v for k, v in trace.items() if k.startswith("hone.")},
            "hone.models.replay_of": call_span["span_id"],  # marks it as a replay (left out of analysis)
            "gen_ai.input.messages": json.dumps(messages),
            "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": output}]),
        }
        return {
            **call_span,
            "trace_id": trace_id,
            "parent_span_id": parent,
            "span_id": secrets.token_hex(8),  # a new call is a new span, with its own id
            "start_time": started.isoformat(),
            "end_time": ended.isoformat(),
            "attributes": new_attributes,
        }


class MyLogSource:
    """RecordSource: model calls from an in-house log, one span per call (`name` + `spans(since=)`)."""

    name = "mylog:support"  # stable: it keys the incremental ingest position

    def __init__(self, rows):
        self.rows = rows

    def spans(self, *, since=None):
        for i, row in enumerate(self.rows):
            if since is None or row["time"] >= since:
                yield {
                    "trace_id": f"{i + 1:032x}",
                    "span_id": f"{i + 1:016x}",
                    "name": "chat",
                    "start_time": row["time"],
                    "end_time": row["time"],
                    "status": {"code": "ok"},
                    "resource": {"service.name": "support_bot"},  # the workflow name
                    "attributes": {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.request.model": "gpt-4o-mini",
                        "gen_ai.output.messages": json.dumps(
                            [{"role": "assistant", "content": row["answer"]}]
                        ),
                    },
                }


log = [{"time": f"2026-09-01T10:{m:02d}:00Z", "answer": f"Answer {m}"} for m in range(10)]
check_text_client(MyTextClient())
check_embedder(MyEmbedder())
check_replayer(MyReplayer())
check_record_source(MyLogSource(log))
print("contract checks passed")

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=300, plant=["homogeneity_from_example"])
    ws = tl.Workspace(Path(tmp) / "lens", llm=MyTextClient(), embedder=MyEmbedder(), replayer=MyReplayer())
    ws.ingest(f"hone:{runs.root}")

    report = ws.analyze("song_ideas", budget="20 calls")
    diversity = next(finding for finding in report.findings if finding.category == "diversity")
    print(f"{diversity.id}: {diversity.title}")
    print("  description:", diversity.details.get("cluster_description"))

    explained = ws.explain(diversity.id)
    assert explained.cause is not None
    print(f"  cause: {explained.cause.kind} {explained.cause.target!r}")
    print(f"  hypothesis: {explained.cause.hypothesis}")

    result = ws.test(explained.id, variants=1, samples=20)
    print(f"  replay test: {result.metric_name} {result.metric_before} -> {result.metric_after}")
    assert result.confirmed

    # Your own record source is ingested like the built-in ones.
    (ingested,) = ws.ingest(MyLogSource(log))
    print(f"{ingested.source}: {ingested.added} calls")
    assert ingested.added == 10
