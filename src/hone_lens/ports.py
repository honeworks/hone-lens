"""Ports (typing.Protocols) hone-lens owns. Shapes and semantics: design/current.md §6."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

PORTS_VERSION = "1"

TraceContext = Mapping[str, str]


@runtime_checkable
class RecordSource(Protocol):
    """Anything that yields spans in the records span shape (current.md §7)."""

    name: str

    def spans(self, *, since: str | None = None) -> Iterable[Mapping[str, Any]]: ...


class TextClient(Protocol):
    """Prompt in, text or structured object out (current.md §6). Used as the analysis LLM."""

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: TraceContext | None = None,
        **params: Any,
    ) -> Any: ...


class Embedder(Protocol):
    """Texts in, L2-normalized vectors out, order preserved (current.md §6)."""

    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str], *, trace: TraceContext | None = None) -> list[list[float]]: ...


class Replayer(Protocol):
    """Re-run one recorded model call with overrides; returns the new call span (current.md §6)."""

    def replay_call(
        self, call_span: Mapping[str, Any], overrides: Mapping[str, Any], *, trace: TraceContext | None = None
    ) -> Mapping[str, Any]: ...


class StepRerunner(Protocol):
    """Re-run workflow steps for some items (current.md §6); returns the new run ids.

    Reserved for multi-step replay (v2); v0.1 accepts it but does not call it.
    """

    def rerun(self, run_id: str, step: str, items: Sequence[str], params: Mapping[str, Any]) -> list[str]: ...


def get(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from a Mapping or an attribute-style object (current.md §6)."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return getattr(obj, key, default)
