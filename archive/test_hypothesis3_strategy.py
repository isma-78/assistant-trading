from dataclasses import dataclass
from unittest.mock import patch

import pytest

from archive.hypothesis3_strategy import (
    TP1_R_MULTIPLE,
    TP2_R_MULTIPLE,
    _evaluate_entry,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import DONCHIAN_PERIOD, MA_PERIOD, TrendSignal


def _candle(close, high=None, low=None):
    high = high if high is not None else close
    low = low if low is not None else close
    return Candle(time_utc="t", open=close, high=high, low=low, close=close)


def _flat_candles(closes):
    return [_candle(c) for c in closes]


# Même construction que tests/test_trend_strategy.py::_regime_setup —
# copiée plutôt qu'importée (chaque fichier de test est autonome, aucun
# précédent d'import inter-fichiers de test dans ce projet).
def _regime_setup(direction: str, breakout: bool):
    if direction == "long":
        base = [_candle(100.0, high=105.0, low=95.0) for _ in range(MA_PERIOD - DONCHIAN_PERIOD - 1)]
        channel = [_candle(100.0, high=105.0, low=95.0) for _ in range(DONCHIAN_PERIOD)]
        current_close = 110.0 if breakout else 102.0
        return base + channel + [_candle(current_close)]
    base = [_candle(100.0, high=105.0, low=95.0) for _ in range(MA_PERIOD - DONCHIAN_PERIOD - 1)]
    channel = [_candle(100.0, high=105.0, low=95.0) for _ in range(DONCHIAN_PERIOD)]
    current_close = 85.0 if breakout else 98.0
    return base + channel + [_candle(current_close)]


# --- bout en bout, fenêtre réelle (aucun double) ---------------------------

def test_evaluate_entry_long_real_window_adds_tp1_tp2():
    candles = _regime_setup("long", breakout=True)
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(110.0)
    assert signal.stop_price == pytest.approx(95.0)
    r = 110.0 - 95.0
    assert signal.tp1 == pytest.approx(110.0 + r)
    assert signal.tp2 == pytest.approx(110.0 + 2 * r)
    assert signal.confidence == 1.0


def test_evaluate_entry_short_real_window_adds_tp1_tp2():
    candles = _regime_setup("short", breakout=True)
    signal = evaluate_entry("GBPUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == pytest.approx(85.0)
    assert signal.stop_price == pytest.approx(105.0)
    r = 105.0 - 85.0
    assert signal.tp1 == pytest.approx(85.0 - r)
    assert signal.tp2 == pytest.approx(85.0 - 2 * r)


def test_evaluate_entry_no_breakout_returns_none():
    candles = _regime_setup("long", breakout=False)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_no_regime_returns_none():
    assert evaluate_entry("EURUSD", _flat_candles([100.0] * MA_PERIOD)) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", None) is None


# --- fail-safe interne (double sur trend_strategy.evaluate_entry) ---------

def test_evaluate_entry_internal_error_is_fail_safe():
    @dataclass(frozen=True)
    class _BadDirectionSignal:
        asset: str
        direction: str
        entry_price: float
        stop_price: float
        confidence: float = 1.0

    with patch(
        "archive.hypothesis3_strategy._trend_evaluate_entry",
        return_value=_BadDirectionSignal(asset="EURUSD", direction="sideways", entry_price=100.0, stop_price=90.0),
    ):
        assert evaluate_entry("EURUSD", []) is None


def test_underscore_evaluate_entry_returns_none_when_no_base_signal():
    with patch("archive.hypothesis3_strategy._trend_evaluate_entry", return_value=None):
        assert _evaluate_entry("EURUSD", []) is None


def test_r_multiples_are_1_and_2():
    assert TP1_R_MULTIPLE == 1.0
    assert TP2_R_MULTIPLE == 2.0
