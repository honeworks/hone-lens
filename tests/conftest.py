"""Shared pytest fixtures: GPU lock and real-model availability checks."""

from __future__ import annotations

import fcntl
import json
import os
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

OLLAMA_URL = os.environ.get("HONE_TEST_OLLAMA_URL", "http://127.0.0.1:11434")


@pytest.fixture(scope="session")
def gpu_lock() -> Iterator[None]:
    """Hold the machine-wide GPU lock for the session (no-op if scripts/gpu-lock.sh already holds it)."""
    if os.environ.get("HONE_GPU_LOCK_HELD") == "1":
        yield
        return
    path = Path(os.environ.get("HONE_GPU_LOCK", "/tmp/honeworks-gpu.lock"))  # noqa: S108 - shared machine-wide lock by design
    path.touch(exist_ok=True)
    with path.open("r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:  # noqa: S310
            return [m["name"] for m in json.load(r).get("models", [])]
    except OSError:
        return []


@pytest.fixture(scope="session")
def ollama_model(gpu_lock: None):
    """Factory: ollama_model("HONE_TEST_TEXT_MODEL", "gemma4-12b:latest") -> name, or skip with a reason."""
    available = ollama_models()

    loaded: list[str] = []

    def _use(env: str, default: str) -> str:
        name = os.environ.get(env, default)
        if not available:
            pytest.skip(f"Ollama not reachable at {OLLAMA_URL}")
        if name not in available:
            pytest.skip(f"Ollama model {name!r} ({env}) not installed")
        loaded.append(name)
        return name

    yield _use
    for name in set(loaded):  # free the shared GPU for the next test run
        try:
            req = urllib.request.Request(  # noqa: S310
                f"{OLLAMA_URL}/api/generate",
                data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=30).read()  # noqa: S310
        except OSError:
            pass
