from dataclasses import dataclass
from unittest.mock import patch

import pytest

from archive.hypothesis2_strategy import (
    TP1_R_MULTIPLE,
    TP2_R_MULTIPLE,
    _evaluate_entry,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close)


# Même fixture, validée en exécutant le code réel, que
# tests/test_ict_strategy.py::_RECENT_LONG (post-bascule du régime
# structurel, 23/08/2026) — copiée plutôt qu'importée (chaque fichier de
# test est autonome). Produit un signal long, entrée=105, stop=90.
_RECENT_LONG = [
    (100, 100, 100), (100, 100, 100), (100, 100, 100), (100, 100, 100),
    (100, 100, 100), (100, 100, 100),
    (97, 90, 93),
    (120, 91, 100), (135, 95, 115), (138, 100, 125), (139, 105, 130),
    (140, 110, 135),
    (125, 101, 115), (110, 95, 100),
    (97, 79, 90), (93, 75, 85),
    (101, 70, 90),
    (95, 65, 80), (90, 60, 75),
    (85, 55, 70), (85, 55, 70), (85, 55, 70), (85, 55, 70),
    (100, 90, 94),
    (109, 103, 105),
]


def _build_candles(recent_triples, baseline_n=0, baseline_value=100.0):
    baseline = [_c(i, baseline_value, baseline_value, baseline_value) for i in range(baseline_n)]
    recent = [_c(baseline_n + i, h, l, c) for i, (h, l, c) in enumerate(recent_triples)]
    return baseline + recent


# --- bout en bout, fenêtre réelle (aucun double) ---------------------------

def test_evaluate_entry_long_real_window_adds_tp1_tp2():
    candles = _build_candles(_RECENT_LONG)
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(105.0)
    assert signal.stop_price == pytest.approx(90.0)
    r = 105.0 - 90.0
    assert signal.tp1 == pytest.approx(105.0 + r)
    assert signal.tp2 == pytest.approx(105.0 + 2 * r)
    assert signal.confidence == 1.0


def test_evaluate_entry_short_real_window_adds_tp1_tp2():
    mirrored = [(200 - low, 200 - high, 200 - close) for (high, low, close) in _RECENT_LONG]
    candles = _build_candles(mirrored)
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == pytest.approx(95.0)
    assert signal.stop_price == pytest.approx(110.0)
    r = 110.0 - 95.0
    assert signal.tp1 == pytest.approx(95.0 - r)
    assert signal.tp2 == pytest.approx(95.0 - 2 * r)


def test_evaluate_entry_no_ict_signal_returns_none():
    candles = _build_candles([(100, 100, 100)] * 25)  # aucun swing -> pas de régime structurel
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", None) is None


# --- fail-safe interne (double sur ict_strategy.evaluate_entry) -----------

def test_evaluate_entry_internal_error_is_fail_safe():
    @dataclass(frozen=True)
    class _BadDirectionSignal:
        asset: str
        direction: str
        entry_price: float
        stop_price: float
        confidence: float = 1.0

    with patch(
        "archive.hypothesis2_strategy._ict_evaluate_entry",
        return_value=_BadDirectionSignal(asset="EURUSD", direction="sideways", entry_price=100.0, stop_price=90.0),
    ):
        assert evaluate_entry("EURUSD", []) is None


def test_underscore_evaluate_entry_returns_none_when_no_base_signal():
    with patch("archive.hypothesis2_strategy._ict_evaluate_entry", return_value=None):
        assert _evaluate_entry("EURUSD", []) is None


def test_r_multiples_are_1_and_2():
    assert TP1_R_MULTIPLE == 1.0
    assert TP2_R_MULTIPLE == 2.0
