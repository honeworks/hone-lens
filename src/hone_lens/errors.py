"""Typed exceptions raised at the public API."""


class HoneLensError(Exception):
    """Base class for every error hone-lens raises on purpose."""


class SourceError(HoneLensError):
    """A record source could not be read (missing file, bad format, unknown source string)."""


class BudgetExceeded(HoneLensError):
    """An LLM or replay stage would spend more than its budget.

    Stages catch this internally, stop, and return partial results with a cost report; it only
    reaches callers who drive a `Budget` themselves.
    """
