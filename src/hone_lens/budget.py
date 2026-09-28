"""`Budget`: how much the LLM and replay stages may spend, and what they spent."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from hone_lens.errors import BudgetExceeded, HoneLensError
from hone_lens.ports import get

_AMOUNT = re.compile(r"^\s*\$?(\d+(?:\.\d+)?)\s*(usd|\$|tokens|calls)?\s*$", re.IGNORECASE)


@dataclass
class Budget:
    """Limits for one stage run; a limit left `None` is unlimited.

    Cost per call: the client's reported `usage["cost_usd"]` when present (an optional extension of the
    port's `usage`), else tokens x `usd_per_1k_tokens / 1000` (0 for local models, so give a `tokens` or
    `calls` limit for those). Limits are checked before each call with an estimate; with reported costs and
    no price the last call can overshoot `usd` by its own cost. A `Budget` is a wallet: pass the same object
    to several calls to share one limit::

        tl.Budget(usd=2.0, usd_per_1k_tokens=0.002)
        tl.Budget(calls=50)
    """

    usd: float | None = None
    tokens: int | None = None
    calls: int | None = None
    usd_per_1k_tokens: float = 0.0
    spent_usd: float = field(default=0.0, init=False)
    spent_tokens: int = field(default=0, init=False)
    spent_calls: int = field(default=0, init=False)

    def check(self, estimated_tokens: int = 0) -> None:
        """Raise `BudgetExceeded` if one more call of about `estimated_tokens` would break a limit."""
        cost = estimated_tokens * self.usd_per_1k_tokens / 1000
        if self.calls is not None and self.spent_calls + 1 > self.calls:
            raise BudgetExceeded(f"call budget of {self.calls} spent")
        if self.tokens is not None and self.spent_tokens + estimated_tokens > self.tokens:
            raise BudgetExceeded(f"token budget of {self.tokens} spent ({self.spent_tokens} used)")
        if self.usd is not None and self.spent_usd + cost > self.usd:
            raise BudgetExceeded(f"budget of ${self.usd:.2f} spent (${self.spent_usd:.4f} used)")

    def mark(self) -> tuple[float, int]:
        """What is spent so far; `since(mark)` gives the spend after it."""
        return self.spent_usd, self.spent_calls

    def since(self, mark: tuple[float, int]) -> tuple[float, int]:
        """(USD, calls) spent since `mark`."""
        return self.spent_usd - mark[0], self.spent_calls - mark[1]

    def charge(self, usage: Mapping[str, Any] | Any) -> None:
        """Count one call with its `usage` (`input_tokens`, `output_tokens`, optional `cost_usd`)."""
        tokens = int(get(usage, "input_tokens") or 0) + int(get(usage, "output_tokens") or 0)
        cost = get(usage, "cost_usd")
        self.spent_calls += 1
        self.spent_tokens += tokens
        self.spent_usd += float(cost) if cost is not None else tokens * self.usd_per_1k_tokens / 1000


def as_budget(value: Budget | float | str | None) -> Budget:
    """A `Budget` from `None` (unlimited), a number (USD), a string (`"2usd"`, `"$2"`, `"5000 tokens"`,
    `"40 calls"`) or a `Budget`."""
    if value is None:
        return Budget()
    if isinstance(value, Budget):
        return value
    if isinstance(value, int | float):
        return Budget(usd=float(value))
    match = _AMOUNT.match(value)
    if not match:
        raise HoneLensError(
            f"budget {value!r} not understood; use e.g. 2.5, '2usd', '5000 tokens' or '40 calls'"
        )
    amount, unit = float(match.group(1)), (match.group(2) or "usd").lower()
    if unit == "tokens":
        return Budget(tokens=int(amount))
    if unit == "calls":
        return Budget(calls=int(amount))
    return Budget(usd=amount)
