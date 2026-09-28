"""One way to ask the analysis LLM (`TextClient` port) for structured JSON, within a budget.

Every analysis prompt sends a short instruction as the system message, the task data as a JSON payload
in the user message, and a JSON Schema whose `title` names the task (D-003).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from hone_lens.budget import Budget
from hone_lens.ports import TextClient, TraceContext, get

MAX_OUTPUT_TOKENS = 400  # the budget estimate per answer; not sent as a cap (thinking models need room)


def ask(
    llm: TextClient,
    task: str,
    instruction: str,
    payload: Mapping[str, Any],
    properties: Mapping[str, Any],
    budget: Budget,
    trace: TraceContext | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """`(answer, None)` or `(None, error)`. Raises `BudgetExceeded` *before* calling when the budget is spent.

    The client's failures (exceptions, `error`, unparsable output) come back as the error string.
    """
    content = json.dumps(payload, ensure_ascii=False, default=str)
    budget.check(len(content) // 4 + len(instruction) // 4 + MAX_OUTPUT_TOKENS)
    schema = {"title": task, "type": "object", "properties": dict(properties), "required": list(properties)}
    messages = [{"role": "system", "content": instruction}, {"role": "user", "content": content}]
    try:
        result = llm.complete(messages, schema=schema, trace=trace, temperature=0.0)
    except Exception as e:  # current.md §6: record the failure, never crash the analysis
        budget.charge({})
        return None, f"{type(e).__name__}: {e}"
    budget.charge(get(result, "usage") or {})
    parsed, error = get(result, "parsed"), get(result, "error")
    if error or not isinstance(parsed, Mapping):
        return None, str(error or "the model returned no JSON object")
    answer = dict(cast(Mapping[str, Any], parsed))
    missing = sorted(set(properties) - set(answer))
    if missing:  # many clients do not validate against the schema
        return None, f"the answer lacks {missing}"
    return answer, None
