"""Tests de src/structural_test.py (Partie 3, 08/10/2026)."""

import pytest

from src import structural_test as st


# --- rank -------------------------------------------------------------------

def test_rank_distinct_values():
    assert st.rank([30, 10, 20]) == [3, 1, 2]


def test_rank_with_ties_averages():
    assert st.rank([10, 20, 20, 30]) == [1, 2.5, 2.5, 4]


def test_rank_all_tied():
    assert st.rank([5, 5, 5]) == [2, 2, 2]


# --- _pearson -----------------------------------------------------------------

def test_pearson_empty():
    assert st._pearson([], []) is None


def test_pearson_zero_variance():
    assert st._pearson([1, 1, 1], [1, 2, 3]) is None


def test_pearson_perfect_positive():
    assert st._pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)


# --- spearman_rho --------------------------------------------------------------

def test_spearman_rho_mismatched_length():
    assert st.spearman_rho([1, 2], [1, 2, 3]) is None


def test_spearman_rho_too_short():
    assert st.spearman_rho([1], [1]) is None


def test_spearman_rho_perfect_monotonic_positive():
    assert st.spearman_rho([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)


def test_spearman_rho_perfect_monotonic_negative():
    assert st.spearman_rho([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_rho_constant_x_returns_none():
    assert st.spearman_rho([7, 7, 7], [1, 2, 3]) is None


# --- permutation_p_value ---------------------------------------------------------

def test_permutation_p_value_undefined_observed():
    assert st.permutation_p_value([7, 7, 7], [1, 2, 3], reps=100) == (None, None)


def test_permutation_p_value_strong_monotonic_small_p():
    x = [1, 2, 3, 4, 5, 6, 7, 8]
    y = [10, 20, 30, 40, 50, 60, 70, 80]
    rho, p = st.permutation_p_value(x, y, reps=2000)
    assert rho == pytest.approx(1.0)
    assert 0.0 <= p <= 0.05


def test_permutation_p_value_no_relationship_large_p():
    x = [1, 2, 3, 4, 5, 6]
    y = [3, 1, 4, 1, 5, 9]
    rho, p = st.permutation_p_value(x, y, reps=2000)
    assert rho is not None
    assert 0.0 <= p <= 1.0


def test_permutation_p_value_deterministic_with_seed():
    x = [1, 2, 3, 4, 5, 6, 7]
    y = [2, 1, 4, 3, 6, 5, 7]
    r1 = st.permutation_p_value(x, y, reps=500, seed=42)
    r2 = st.permutation_p_value(x, y, reps=500, seed=42)
    assert r1 == r2
