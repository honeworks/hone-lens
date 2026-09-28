import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hone_lens.stats import group_by, mann_whitney_p, two_proportion_p


def test_group_by_keeps_first_seen_order() -> None:
    rows = [{"a": 2, "b": 1}, {"a": 1, "b": 1}, {"a": 2, "b": 2}]
    assert list(group_by(rows, "a")) == [(2,), (1,)]
    assert group_by(rows, "a", "b")[(2, 1)] == [rows[0]]


@given(st.integers(0, 50), st.integers(1, 50), st.integers(0, 50), st.integers(1, 50))
def test_p_values_are_probabilities(a: int, n_a: int, b: int, n_b: int) -> None:
    assert 0.0 <= two_proportion_p(min(a, n_a), n_a, min(b, n_b), n_b) <= 1.0
    assert 0.0 <= mann_whitney_p(list(range(n_a)), list(range(b, b + n_b))) <= 1.0


def test_two_proportion_p() -> None:
    assert two_proportion_p(50, 100, 50, 100) == pytest.approx(1.0)
    assert two_proportion_p(10, 1000, 60, 1000) < 1e-6
    assert two_proportion_p(0, 0, 1, 10) == 1.0  # no data: no evidence
    assert two_proportion_p(0, 10, 0, 10) == 1.0  # no variance


def test_mann_whitney_p() -> None:
    rng = random.Random(0)
    a = [rng.gauss(0, 1) for _ in range(200)]
    b = [rng.gauss(0, 1) for _ in range(200)]
    assert mann_whitney_p(a, b) > 0.01
    assert mann_whitney_p(a, [x + 1 for x in b]) < 1e-6
    assert mann_whitney_p([], b) == 1.0
    assert mann_whitney_p([1, 1, 2], [1, 2, 2]) > 0.05  # ties get average ranks
