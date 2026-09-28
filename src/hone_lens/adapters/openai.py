"""`TextClient` and `Embedder` over the OpenAI Python SDK (`pip install hone-lens[openai]`).

Works with any OpenAI-compatible server: OpenAI, Ollama (`base_url="http://127.0.0.1:11434/v1"`), vLLM,
LM Studio. The SDK reads `OPENAI_API_KEY` / `OPENAI_BASE_URL` when they are not given::

    llm = OpenAITextClient("gpt-4o-mini")
    embedder = OpenAIEmbedder("text-embedding-3-small")
    ws = hone_lens.Workspace(".hone/lens", llm=llm, embedder=embedder)
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletion

from hone_lens.ports import TraceContext

_PARAMS = ("temperature", "top_p", "seed", "max_tokens")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _client(base_url: str | None, api_key: str | None) -> OpenAI:
    # local servers need no key, but the SDK insists on one
    return OpenAI(base_url=base_url, api_key=api_key or os.environ.get("OPENAI_API_KEY") or "not-needed")


class OpenAITextClient:
    """Chat completions; with a `schema`, asks for JSON that follows it and parses the answer."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or _client(base_url, api_key)

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: TraceContext | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """A `TextResult` mapping (current.md §6). Transport errors raise; bad JSON is an `error`."""
        kwargs: dict[str, Any] = {k: params[k] for k in _PARAMS if k in params}
        if schema is not None:
            name = re.sub(r"[^A-Za-z0-9_-]", "_", str(schema.get("title") or "answer"))
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": dict(schema)},
            }
        messages_: Any = list(messages)
        response = cast(
            ChatCompletion,
            self.client.chat.completions.create(model=self.model, messages=messages_, **kwargs),
        )
        choice = response.choices[0]
        text = choice.message.content or ""
        parsed, error = None, None
        if schema is not None:
            parsed, error = _parse(text, schema)
        usage = response.usage
        return {
            "text": text,
            "parsed": parsed,
            "error": error,
            "model": response.model,
            "finish_reason": choice.finish_reason,
            "usage": {"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
            if usage
            else {},
            "span_id": None,
        }


def _parse(text: str, schema: Mapping[str, Any]) -> tuple[Any, str | None]:
    """The JSON answer when it is an object with the schema's required keys, else an error."""
    try:
        value = json.loads(_FENCE.sub("", text.strip()))
    except ValueError as e:
        return None, f"the answer is not JSON ({e})"
    if not isinstance(value, dict):
        return None, "the answer is not a JSON object"
    missing = sorted(set(schema.get("required") or []) - set(value))  # pyright: ignore[reportUnknownArgumentType]
    return (None, f"the answer lacks {missing}") if missing else (value, None)  # pyright: ignore[reportUnknownVariableType]


class OpenAIEmbedder:
    """Embeddings endpoint; vectors are L2-normalized (current.md §6). `dimensions` is asked from the
    server on first use when not given."""

    def __init__(
        self,
        model: str,
        *,
        dimensions: int | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.model_id = model
        self.client = client or _client(base_url, api_key)
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._dimensions = len(self.embed(["dimensions"])[0])
        return self._dimensions

    def embed(self, texts: Sequence[str], *, trace: TraceContext | None = None) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model_id, input=list(texts))
        vectors = [list(item.embedding) for item in sorted(response.data, key=lambda d: d.index)]
        return [[x / (math.sqrt(sum(v * v for v in vector)) or 1.0) for x in vector] for vector in vectors]
