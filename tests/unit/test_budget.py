import pytest

import hone_lens as tl
from hone_lens.budget import as_budget
from hone_lens.errors import BudgetExceeded, HoneLensError
from hone_lens.llm import ask
from hone_lens.testing import FakeTextClient


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, tl.Budget()),
        (2, tl.Budget(usd=2.0)),
        ("2usd", tl.Budget(usd=2.0)),
        ("$1.5", tl.Budget(usd=1.5)),
        ("5000 tokens", tl.Budget(tokens=5000)),
        ("40 calls", tl.Budget(calls=40)),
    ],
)
def test_as_budget(value, expected) -> None:
    assert as_budget(value) == expected


def test_as_budget_rejects_nonsense_and_passes_budgets_through() -> None:
    b = tl.Budget(calls=1)
    assert as_budget(b) is b
    with pytest.raises(HoneLensError, match="budget 'lots' not understood"):
        as_budget("lots")


def test_limits_and_charges() -> None:
    b = tl.Budget(tokens=100, usd_per_1k_tokens=0.1)
    b.check(50)
    b.charge({"input_tokens": 30, "output_tokens": 10})
    assert (b.spent_calls, b.spent_tokens, b.spent_usd) == (1, 40, pytest.approx(0.004))
    with pytest.raises(BudgetExceeded, match="token budget of 100 spent"):
        b.check(61)
    money = tl.Budget(usd=0.01, usd_per_1k_tokens=1.0)
    money.charge({"input_tokens": 5, "cost_usd": 0.009})  # a reported cost wins over the token price
    assert money.spent_usd == 0.009
    money.check(1)
    with pytest.raises(BudgetExceeded, match=r"budget of \$0.01 spent"):
        money.check(2)
    calls = tl.Budget(calls=1)
    calls.charge({})
    with pytest.raises(BudgetExceeded, match="call budget of 1 spent"):
        calls.check()


def test_unlimited_budget_counts_but_never_stops() -> None:
    b = tl.Budget()
    for _ in range(100):
        b.check(10_000)
        b.charge({"input_tokens": 10_000})
    assert b.spent_calls == 100 and b.spent_usd == 0.0


def test_ask_success_errors_and_budget() -> None:
    llm = FakeTextClient([{"answer": 1}])
    b = tl.Budget(calls=2)
    answer, error = ask(llm, "task", "Do it.", {"x": 1}, {"answer": {"type": "integer"}}, b)
    assert (answer, error) == ({"answer": 1}, None)
    call = llm.calls[0]
    assert call["schema"]["title"] == "task" and call["schema"]["required"] == ["answer"]
    assert call["messages"][0] == {"role": "system", "content": "Do it."}
    assert call["messages"][1]["content"] == '{"x": 1}' and call["params"]["temperature"] == 0.0

    answer, error = ask(FakeTextClient(["not json"]), "task", "i", {}, {}, b)
    assert answer is None and error == "response is not valid JSON for the schema"
    with pytest.raises(BudgetExceeded):
        ask(llm, "task", "i", {}, {}, b)


def test_ask_turns_client_exceptions_into_errors() -> None:
    def boom(messages, schema):
        raise ConnectionError("no server")

    b = tl.Budget()
    assert ask(FakeTextClient(rule=boom), "t", "i", {}, {}, b) == (None, "ConnectionError: no server")
    assert b.spent_calls == 1
    assert ask(FakeTextClient(["[1, 2]"]), "t", "i", {}, {}, b) == (None, "the model returned no JSON object")


def test_ask_rejects_answers_without_the_required_keys() -> None:
    answer, error = ask(
        FakeTextClient([{"description": "x"}]),
        "t",
        "i",
        {},
        {"description": {}, "in_common": {}},
        tl.Budget(),
    )
    assert answer is None and error == "the answer lacks ['in_common']"


def test_spent_counters_are_not_constructor_arguments() -> None:
    with pytest.raises(TypeError):
        tl.Budget(spent_usd=5)  # type: ignore[call-arg]
