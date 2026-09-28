# Bring your own adapter

hone-lens talks to models, trace stores and workflows through five small protocols (`typing.Protocol`, defined in
`hone_lens.ports`). Any object with the right methods works: no base class, no registration, no
dependency on hone-lens in your code. Each protocol has a contract checker in `hone_lens.testing` that
raises `AssertionError` with a message when an implementation breaks the contract; run it in your tests.

| Port | Used for | Checker | Fake |
|---|---|---|---|
| `TextClient` | the analysis LLM (`Workspace(llm=...)`) | `check_text_client` | `FakeTextClient` |
| `Embedder` | output embeddings (`Workspace(embedder=...)`) | `check_embedder` | `FakeEmbedder` |
| `Replayer` | replay tests (`Workspace(replayer=...)`) | `check_replayer` | `FakeReplayer` |
| `RecordSource` | reading spans (`ws.ingest(source)`) | `check_record_source` | `FakeRecordSource` |
| `StepRerunner` | rerunning workflow steps (`Workspace(step_rerunner=...)`; reserved for v2 multi-step replay) | `check_step_rerunner` | `FakeStepRerunner` |

`hone_lens.PORTS_VERSION` (`"1"`) names the version of these contracts.

## TextClient

```text
def complete(self, messages, *, schema=None, trace=None, **params) -> TextResult
```

`messages` are chat messages (`{"role", "content"}`). With a `schema` (a JSON Schema object), the client
should return the answer parsed as `parsed`. The result can be a mapping or an object with these fields:
`text`, `parsed`, `error` (a string when the call failed or the answer is not valid JSON, else `None`),
`model`, `finish_reason`, `usage` (`{"input_tokens", "output_tokens"}`, optionally `"cost_usd"`, which
budgets then use), `span_id`. Ignore parameters you do not know. Raising is allowed: hone-lens turns
exceptions and errors into report notes and goes on.

How hone-lens calls it: every analysis prompt is a system instruction plus one user message holding the
task data as JSON, with `temperature=0.0` and a schema whose `title` names the task
(`cluster_description`, `trace_note`, `taxonomy`, `label`, `hypothesis`, `rewrite`). No `max_tokens` is
sent. An answer that is not an object with the schema's required keys is treated as a failure.

```python
import json

from hone_lens.testing import check_text_client


class MyTextClient:
    """Wraps any function that turns chat messages into text."""

    def __init__(self, generate, model="my-model"):
        self.generate, self.model = generate, model

    def complete(self, messages, *, schema=None, trace=None, **params):
        text = self.generate(messages)
        parsed, error = None, None
        if schema is not None:
            try:
                parsed = json.loads(text)
            except ValueError as e:
                error = f"the answer is not JSON ({e})"
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        usage = {"input_tokens": chars // 4, "output_tokens": len(text) // 4}
        return {
            "text": text,
            "parsed": parsed,
            "error": error,
            "model": self.model,
            "finish_reason": "stop",
            "usage": usage,
            "span_id": None,
        }


check_text_client(MyTextClient(lambda messages: '{"ok": true}'))
```

## Embedder

```text
model_id: str
dimensions: int
def embed(self, texts, *, trace=None) -> list[list[float]]
```

One L2-normalized vector of `dimensions` floats per text, in order; an empty list in gives an empty list
out. `model_id` keys the embedding cache in the workspace: change it when the vectors change.

```python
import hashlib
import math

from hone_lens.testing import check_embedder


class WordHashEmbedder:
    """A toy embedder: hashed bag of words. Replace the body of embed() with your model call."""

    model_id = "word-hash-v1"
    dimensions = 128

    def embed(self, texts, *, trace=None):
        vectors = []
        for text in texts:
            v = [0.0] * self.dimensions
            for word in text.lower().split():
                v[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v] if any(v) else [1.0] + [0.0] * (self.dimensions - 1))
        return vectors


check_embedder(WordHashEmbedder())
```

## Replayer

```text
def replay_call(self, call_span, overrides, *, trace=None) -> span
```

Re-run one recorded model call with `overrides` and everything else unchanged, and return the new call as
a span (same shape as the records hone-lens reads). The returned span needs its own `span_id`,
`hone.models.replay_of` = the original span id, and the new output in `gen_ai.output.messages`. Put the
`trace` context (`traceparent`, `hone.lens.finding_id`) on the new call so the replay can be traced back.

hone-lens sends `{"prompt.sections": {section_id: new_text_or_None}}` (`None` removes the section). The
section positions are in `hone.models.prompt.sections` (character offsets in the rendered prompt), so a
replayer can rebuild the prompt like this:

```python
import json
import secrets

from hone_lens.testing import check_replayer


def rebuild(prompt, sections, replacements):
    """The prompt with sections replaced (None: removed), last section first so offsets stay valid."""
    for s in sorted(sections, key=lambda s: s["start"], reverse=True):
        if s["id"] in replacements:
            prompt = prompt[: s["start"]] + (replacements[s["id"]] or "") + prompt[s["end"] :]
    return prompt


class MyReplayer:
    def __init__(self, generate):
        self.generate = generate  # prompt text -> output text (your model call)

    def replay_call(self, call_span, overrides, *, trace=None):
        a = call_span["attributes"]
        messages = json.loads(a["gen_ai.input.messages"])
        sections = json.loads(a.get("hone.models.prompt.sections", "[]"))
        prompt = rebuild(messages[-1]["content"], sections, overrides.get("prompt.sections", {}))
        output = self.generate(prompt)
        attributes = {
            **a,
            **(trace or {}),
            "hone.models.replay_of": call_span["span_id"],
            "gen_ai.input.messages": json.dumps([*messages[:-1], {**messages[-1], "content": prompt}]),
            "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": output}]),
        }
        return {**call_span, "span_id": secrets.token_hex(8), "attributes": attributes}


check_replayer(MyReplayer(lambda prompt: f"An idea written from {len(prompt)} characters of prompt."))
```

In a real setup, hone-models' `Replayer` does this: it re-renders the prompt with the section overrides,
calls the same model with the same parameters, and records the new call.

## RecordSource

```text
name: str
def spans(self, *, since=None) -> Iterable[span]
```

See [sources.md](sources.md#your-own-source) for a full example. `name` must be stable: it keys the
incremental ingest position.

A source may also have `step_records(*, since=None) -> list[dict]`: rows for the `flow_steps` table
(columns in [detectors.md](detectors.md#analytics-tables)). `ws.ingest` calls it when it exists and
replaces the rows of every run it returns. hone-lens' `FlowRuns` (extra `flow`) is such a source.

## StepRerunner

```text
def rerun(self, run_id: str, step: str, items: Sequence[str], params: Mapping) -> list[str]
```

Rerun `step` (and everything downstream of it) of run `run_id` for `items`, with `params` merged over the
run's params, and return the id(s) of the new run(s): the source run is never changed. With hone-flow this
is a fork, which `hone_lens.adapters.flow.FlowStepRerunner(workflow)` does:
`[workflow.open_run(run_id).fork(refresh=(step,), items=items, params=params).run_id]`.
`hone_lens.testing.FakeFlowRuns` has the same `open_run(...).fork(...)` shape, so it can stand in for the
workflow:

```python
from hone_lens.adapters.flow import FlowStepRerunner
from hone_lens.testing import FakeFlowRuns, check_step_rerunner

workflow = FakeFlowRuns()  # in real use: the hone-flow Workflow the runs belong to
workflow.add_run("run-1", [{"step": "lyrics", "item": "01"}, {"step": "render", "item": "01"}])
rerunner = FlowStepRerunner(workflow)
print(rerunner.rerun("run-1", "lyrics", ["01"], {"style": "noir"}))  # ['run-1-fork-1']
print(workflow.forks[0])
# {'run_id': 'run-1', 'refresh': ('lyrics',), 'items': ['01'], 'params': {'style': 'noir'}}
check_step_rerunner(rerunner, "run-1", "lyrics", ["01"])
```

## Using your adapters

Pass them to the workspace. Here the toy embedder above finds the planted pattern:

```python
import hone_lens as tl
from hone_lens.testing import synthetic_runs

runs = synthetic_runs("mine", n_runs=200, plant=["homogeneity_from_example"])
ws = tl.Workspace("mine/lens", embedder=WordHashEmbedder())
ws.ingest(f"hone:{runs.root}")
report = ws.analyze("song_ideas")
print([f.title for f in report.findings])
print(report.notes)  # says the describe stage was skipped: no llm
assert any(f.category == "diversity" for f in report.findings)
```

## The OpenAI-compatible adapter

`pip install "hone-lens[openai]"` adds `hone_lens.adapters.openai`, built on the OpenAI Python SDK:

- `OpenAITextClient(model, *, base_url=None, api_key=None, client=None)`: chat completions; with a schema
  it asks for `response_format=json_schema` and parses the answer (fenced JSON is accepted).
- `OpenAIEmbedder(model, *, dimensions=None, base_url=None, api_key=None, client=None)`: the embeddings
  endpoint, vectors normalized; `dimensions` is asked from the server on first use when not given.

They work with any OpenAI-compatible server: OpenAI, Ollama (`/v1`), vLLM, LM Studio. The SDK reads
`OPENAI_API_KEY` and `OPENAI_BASE_URL` when `api_key` / `base_url` are not given; local servers need no
key.

<!-- not-run -->
```python
import hone_lens as tl
from hone_lens.adapters.openai import OpenAIEmbedder, OpenAITextClient

ollama = "http://127.0.0.1:11434/v1"
ws = tl.Workspace(
    ".hone/lens",
    llm=OpenAITextClient("qwen3:8b", base_url=ollama),
    embedder=OpenAIEmbedder("embeddinggemma", base_url=ollama),
)
ws.ingest("hone:.hone")
report = ws.analyze("song_ideas", budget="40 calls")  # local models cost $0: limit calls or tokens
```

## Entry points (for the CLI)

The CLI cannot receive Python objects, so it finds ports by name in three entry-point groups:

| Group | CLI option |
|---|---|
| `hone.text_clients` | `--llm NAME[:ARG]` |
| `hone.embedders` | `--embedder NAME[:ARG]` |
| `hone.replayers` | `--replayer NAME[:ARG]` |

The entry point is a factory (a class or function). The CLI calls it with `ARG` as one string, or with no
arguments when there is no `:ARG`. hone-lens registers `openai` in the first two groups, so
`--llm openai:gpt-4o-mini` makes `OpenAITextClient("gpt-4o-mini")` (set `OPENAI_BASE_URL` for another
server). hone-lens registers no replayer; hone-models (or your package) provides one. To register yours,
in your package's `pyproject.toml`:

```toml
[project.entry-points."hone.text_clients"]
mymodel = "my_package.lens:MyTextClient"

[project.entry-points."hone.replayers"]
mymodel = "my_package.lens:make_replayer"
```

Then `hone-lens analyze song_ideas --llm mymodel:some-model-id`.
