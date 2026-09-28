"""The OpenAI SDK adapters pass the TextClient / Embedder contracts over a fake HTTP transport.

The OpenAI SDK (3.x) sends through its own `httpx2`, so the fake transport is `httpx2.MockTransport`.
"""

import json
from collections.abc import Callable

import pytest

pytest.importorskip("openai")  # the openai extra (it brings httpx2)

import httpx2 as httpx
from openai import OpenAI

from hone_lens.adapters.openai import OpenAIEmbedder, OpenAITextClient
from hone_lens.testing import check_embedder, check_text_client

BASE = "http://llm.test/v1"


def _completion(content: str, finish: str = "stop") -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m-1",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
    }


def _chat(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    wants_json = "response_format" in body
    return httpx.Response(200, json=_completion('```json\n{"ok": true}\n```' if wants_json else "OK"))


def _embeddings(request: httpx.Request) -> httpx.Response:
    texts = json.loads(request.content)["input"]
    data = [
        {"object": "embedding", "index": i, "embedding": [3.0, 4.0, float(len(t))]}
        for i, t in enumerate(texts)
    ]
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": data[::-1],
            "model": "e",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )


class Server:
    """A fake OpenAI-compatible server: one handler per path, every request kept."""

    def __init__(self, **handlers: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handlers = handlers
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handlers[request.url.path.rsplit("/", 1)[-1]](request)

    def client(self) -> OpenAI:
        transport = httpx.MockTransport(self)
        return OpenAI(
            base_url=BASE, api_key="k", max_retries=0, http_client=httpx.Client(transport=transport)
        )


def test_text_client_contract_and_fields() -> None:
    server = Server(completions=_chat)
    client = OpenAITextClient("m", client=server.client())
    check_text_client(client)
    r = client.complete(
        [{"role": "user", "content": "x"}],
        schema={"title": "trace note", "type": "object"},
        temperature=0.1,
        seed=3,
        junk=1,
    )
    assert r["parsed"] == {"ok": True} and r["error"] is None and r["model"] == "m-1"
    assert r["usage"] == {"input_tokens": 11, "output_tokens": 4} and r["finish_reason"] == "stop"
    sent = json.loads(server.requests[-1].content)
    assert sent["temperature"] == 0.1 and sent["seed"] == 3 and "junk" not in sent
    assert sent["response_format"]["json_schema"]["name"] == "trace_note"


def test_text_client_bad_json_is_an_error_and_transport_errors_raise() -> None:
    truncated = Server(completions=lambda r: httpx.Response(200, json=_completion('{"ok": tru', "length")))
    r = OpenAITextClient("m", client=truncated.client()).complete(
        [{"role": "user", "content": "x"}], schema={"type": "object"}
    )
    assert (
        r["parsed"] is None
        and r["error"].startswith("the answer is not JSON")
        and r["finish_reason"] == "length"
    )
    missing = Server(completions=lambda r: httpx.Response(404, json={"error": {"message": "no such model"}}))
    with pytest.raises(Exception, match="no such model"):
        OpenAITextClient("m", client=missing.client()).complete([{"role": "user", "content": "x"}])


def test_embedder_contract_order_and_dimensions() -> None:
    server = Server(embeddings=_embeddings)
    e = OpenAIEmbedder("e", client=server.client())
    assert e.dimensions == 3 and len(server.requests) == 1  # asked once, on first use
    check_embedder(e)
    a, b = e.embed(["a", "bbbb"])
    assert a[2] < b[2]  # order kept although the server answered in reverse
    assert OpenAIEmbedder("e", dimensions=3, client=server.client()).dimensions == 3


def test_default_clients_read_the_url_and_key() -> None:
    llm = OpenAITextClient("m", base_url=BASE)
    assert str(llm.client.base_url).rstrip("/") == BASE and llm.client.api_key
    assert OpenAIEmbedder("e", base_url=BASE, api_key="secret").client.api_key == "secret"


def test_structured_answers_must_be_objects_with_the_required_keys() -> None:
    schema = {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a", "b"]}
    for content, error in (
        ('["a"]', "the answer is not a JSON object"),
        ('{"a": 1}', "the answer lacks ['b']"),
    ):
        server = Server(completions=lambda r, c=content: httpx.Response(200, json=_completion(c)))
        r = OpenAITextClient("m", client=server.client()).complete(
            [{"role": "user", "content": "x"}], schema=schema
        )
        assert (r["parsed"], r["error"]) == (None, error)
