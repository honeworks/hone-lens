"""The fakes satisfy the port contracts, and the checkers reject broken implementations."""

import pytest

import hone_lens
from hone_lens import ports
from hone_lens.testing import (
    FakeEmbedder,
    FakeRecordSource,
    FakeReplayer,
    FakeTextClient,
    check_embedder,
    check_record_source,
    check_replayer,
    check_text_client,
)
from hone_lens.testing.contracts import example_call_span


def test_ports_version() -> None:
    assert ports.PORTS_VERSION == "1"


@pytest.mark.parametrize("client", [FakeTextClient(), FakeTextClient.analyst()])
def test_text_clients(client) -> None:
    check_text_client(client)


@pytest.mark.parametrize("embedder", [FakeEmbedder(), FakeEmbedder.semantic()])
def test_embedders(embedder) -> None:
    check_embedder(embedder)


@pytest.mark.parametrize("replayer", [FakeReplayer(), FakeReplayer.removing_section_reduces_similarity()])
def test_replayers(replayer) -> None:
    check_replayer(replayer)


def test_fake_record_source() -> None:
    spans = [
        {**example_call_span(), "span_id": f"{i:016x}", "start_time": f"2026-09-27T14:03:1{i}.000Z"}
        for i in range(4)
    ]
    source = FakeRecordSource(spans)
    assert isinstance(source, ports.RecordSource)
    check_record_source(source)


def test_record_source_checker_rejects_bad_ids() -> None:
    bad = FakeRecordSource([{**example_call_span(), "span_id": "not-hex"}])
    with pytest.raises(AssertionError, match="span_id"):
        check_record_source(bad)


def test_replayer_checker_rejects_missing_replay_of() -> None:
    class Echo:
        def replay_call(self, call_span, overrides, *, trace=None):
            return {**call_span, "span_id": "0" * 16}

    with pytest.raises(AssertionError, match="replay_of"):
        check_replayer(Echo())


def test_embedder_checker_rejects_unnormalized() -> None:
    class Raw:
        model_id, dimensions = "raw", 2

        def embed(self, texts, *, trace=None):
            return [[1.0, 1.0] for _ in texts]

    with pytest.raises(AssertionError):
        check_embedder(Raw())


def test_text_client_checker_rejects_bad_shapes() -> None:
    class NotText:
        def complete(self, messages, *, schema=None, trace=None, **params):
            return {"text": 42}

    class NoParsed:
        def complete(self, messages, *, schema=None, trace=None, **params):
            return {"text": "x", "parsed": None, "error": None}

    class Strict:
        def complete(self, messages, *, schema=None, trace=None):
            return {"text": "x", "parsed": {"ok": True}}

    for bad in (NotText(), NoParsed()):
        with pytest.raises(AssertionError):
            check_text_client(bad)
    with pytest.raises(TypeError):
        check_text_client(Strict())


def test_record_source_checker_rejects_ignored_since_and_duplicates() -> None:
    spans = [
        {**example_call_span(), "span_id": f"{i:016x}", "start_time": f"2026-09-27T14:03:1{i}.000Z"}
        for i in range(4)
    ]

    class IgnoresSince(FakeRecordSource):
        def spans(self, *, since=None):
            return super().spans()

    with pytest.raises(AssertionError, match="older spans"):
        check_record_source(IgnoresSince(spans))
    with pytest.raises(AssertionError, match="unique"):
        check_record_source(FakeRecordSource([spans[0], spans[0]]))


def test_replayer_checker_rejects_reused_span_id() -> None:
    class Same:
        def replay_call(self, call_span, overrides, *, trace=None):
            return {
                **call_span,
                "attributes": {**call_span["attributes"], "hone.models.replay_of": call_span["span_id"]},
            }

    with pytest.raises(AssertionError, match="own span id"):
        check_replayer(Same())


def test_embedder_checker_rejects_wrong_dimensions_and_empty() -> None:
    class Wrong:
        model_id, dimensions = "w", 3

        def embed(self, texts, *, trace=None):
            return [[1.0, 0.0] for _ in texts]

    class NotEmpty(FakeEmbedder):
        def embed(self, texts, *, trace=None):
            return super().embed(texts or ["x"])

    for bad in (Wrong(), NotEmpty()):
        with pytest.raises(AssertionError):
            check_embedder(bad)


def test_public_package_exports_ports_version_and_errors() -> None:
    assert hone_lens.PORTS_VERSION == "1"
    assert issubclass(hone_lens.errors.SourceError, hone_lens.errors.HoneLensError)
    assert issubclass(hone_lens.errors.BudgetExceeded, hone_lens.errors.HoneLensError)
