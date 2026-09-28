"""How stage 4 talks to the person reviewing: `ConsoleIO` (terminal) or any object with `show` / `ask`
(for example `hone_lens.testing.ScriptedIO` in tests)."""

from __future__ import annotations

import sys
from typing import Protocol


class ReviewIO(Protocol):
    def show(self, text: str) -> None: ...

    def ask(self, question: str, default: str = "") -> str: ...


class ConsoleIO:
    """Prints to stdout and reads answers from stdin. Without a terminal (stdin is not a TTY) it never
    waits: every question gets its default answer."""

    def show(self, text: str) -> None:
        print(text)

    def ask(self, question: str, default: str = "") -> str:
        if not sys.stdin.isatty():
            return default
        return input(f"{question} ").strip() or default
