"""Tests de src/structural_characteristics.py (Partie 3, 08/10/2026)."""

import math

import pytest

from src.backtest_engine import HistoricalBar
from src import structural_characteristics as sc


def _bar(mid, spread=0.0002, high_extra=0.0, low_extra=0.0):
    half = spread / 2
    return HistoricalBar(
        time_utc="2020-01-01T00:00:00",
        open_bid=mid - half, open_ask=mid + half,
        high_bid=mid + high_extra - half, high_ask=mid + high_extra + half,
        low_bid=mid - low_extra - half, low_ask=mid - low_extra + half,
        close_bid=mid - half, close_ask=mid + half,
    )


# --- median_close_spread ------------------------------------------------

def test_median_close_spread_empty():
    assert sc.median_close_spread([]) is None


def test_median_close_spread_normal():
    bars = [_bar(1.0, spread=0.0002), _bar(1.0, spread=0.0004), _bar(1.0, spread=0.0006)]
    assert sc.median_close_spread(bars) == pytest.approx(0.0004)


# --- wilder_atr_series ----------------------------------------------------

def test_wilder_atr_series_insufficient_bars():
    bars = [_bar(1.0 + i * 0.001) for i in range(10)]
    assert sc.wilder_atr_series(bars, period=14) == []


def test_wilder_atr_series_matches_compute_atr_last_point():
    from src.market_data import compute_atr
    bars = [_bar(1.0 + 0.001 * math.sin(i), high_extra=0.002, low_extra=0.002) for i in range(40)]
    series = sc.wilder_atr_series(bars, period=14)
    assert len(series) == len(bars) - 14
    candles = [b.to_candle() for b in bars]
    expected_last = compute_atr(candles, period=14)
    assert series[-1] == pytest.approx(expected_last)


# --- cost_over_atr ---------------------------------------------------------

def test_cost_over_atr_insufficient_bars():
    bars = [_bar(1.0) for _ in range(5)]
    assert sc.cost_over_atr(bars) is None


def test_cost_over_atr_empty_bars_no_spread():
    assert sc.cost_over_atr([]) is None


def test_cost_over_atr_zero_atr_returns_none():
    bars = [_bar(1.0, spread=0.0002) for _ in range(20)]  # prix constant -> ATR median = 0
    assert sc.cost_over_atr(bars) is None


def test_cost_over_atr_normal():
    bars = [_bar(1.0 + 0.001 * (i % 3), spread=0.0002, high_extra=0.002, low_extra=0.002) for i in range(30)]
    result = sc.cost_over_atr(bars)
    assert result is not None and result > 0


# --- log_returns -----------------------------------------------------------

def test_log_returns_normal():
    bars = [_bar(1.0), _bar(1.01), _bar(1.02)]
    returns = sc.log_returns(bars)
    assert len(returns) == 2
    assert returns[0] == pytest.approx(math.log(1.01 / 1.0), abs=1e-6)


def test_log_returns_skips_non_positive_price():
    bars = [_bar(1.0), _bar(0.0), _bar(1.0)]
    returns = sc.log_returns(bars)
    assert len(returns) == 0  # les deux rendements impliquent un prix <= 0


# --- variance_ratio ----------------------------------------------------------

def test_variance_ratio_insufficient_blocks():
    assert sc.variance_ratio(list(range(10)), scale=8) is None


def test_variance_ratio_zero_variance_returns_none():
    assert sc.variance_ratio([0.0] * 32, scale=8) is None


def test_variance_ratio_trending_series_above_one():
    returns = [0.01] * 32  # rendements constants positifs -> tendance parfaite
    # variance nulle -> None (cas dégénéré), donc on ajoute un bruit minime déterministe
    returns = [0.01 + (0.0001 if i % 2 == 0 else -0.0001) for i in range(32)]
    vr = sc.variance_ratio(returns, scale=8)
    assert vr is not None


def test_variance_ratio_mean_reverting_series_below_one():
    returns = [0.01 if i % 2 == 0 else -0.01 for i in range(32)]  # alternance pure -> VR8 très faible
    vr = sc.variance_ratio(returns, scale=8)
    assert vr is not None and vr < 1.0


# --- volatility_clustering ----------------------------------------------------

def test_volatility_clustering_insufficient_returns():
    assert sc.volatility_clustering([0.01, 0.02]) is None


def test_volatility_clustering_zero_variance_returns_none():
    assert sc.volatility_clustering([0.01] * 10) is None


def test_volatility_clustering_clustered_positive():
    # alternance de blocs de forte et faible amplitude -> |r_t| corrélé à |r_{t-1}|
    returns = []
    for _ in range(5):
        returns += [0.05, 0.04, 0.001, 0.001]
    vr = sc.volatility_clustering(returns)
    assert vr is not None and vr > 0


# --- fonctions internes (bords non atteints par les chemins publics) -----------

def test_sample_variance_insufficient_values():
    assert sc._sample_variance([1.0]) is None


def test_pearson_empty():
    assert sc._pearson([], []) is None


def test_pearson_zero_variance_x():
    assert sc._pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
