"""
tests/test_hypothesis5_strategy_v2.py — H5/L5 (refonte 29/08/2026, voir
docs/HYPOTHESES.md).
"""

import pytest

from src.hypothesis5_strategy_v2 import (
    BOLLINGER_PERIOD,
    COMPRESSION_DURATION,
    COMPRESSION_PERCENTILE,
    PERCENTILE_LOOKBACK,
    STOP_BUFFER_PCT,
    _is_compressed,
    _percentile,
    compute_bollinger_band_at,
    compute_normalized_width_series,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, h, l, c):
    return Candle(time_utc=str(i), open=c, high=h, low=l, close=c)


def _compression_breakout_series(direction="long", breakout=True):
    """100 bougies de référence à volatilité modérée, puis
    COMPRESSION_DURATION bougies très resserrées, puis une bougie de
    cassure (ou non, si `breakout=False`) — construction vérifiée
    numériquement pendant le développement (voir docs/DECISIONS.md)."""
    candles, t = [], 0
    for i in range(120):
        price = 100 + (5 if i % 2 == 0 else -5) * 0.5
        candles.append(_c(t, price + 1, price - 1, price)); t += 1
    for i in range(COMPRESSION_DURATION):
        price = 100 + (0.05 if i % 2 == 0 else -0.05)
        candles.append(_c(t, price + 0.1, price - 0.1, price)); t += 1
    if not breakout:
        candles.append(_c(t, 100.1, 99.9, 100.0)); t += 1  # reste dans la bande
    elif direction == "long":
        candles.append(_c(t, 130, 99, 125)); t += 1
    else:
        candles.append(_c(t, 101, 70, 75)); t += 1
    return candles


# ---------------------------------------------------------------------------
# compute_bollinger_band_at / compute_normalized_width_series / _percentile
# ---------------------------------------------------------------------------

def test_compute_bollinger_band_at_none_before_enough_history():
    candles = [_c(i, 101, 99, 100.0) for i in range(BOLLINGER_PERIOD)]
    assert compute_bollinger_band_at(candles, BOLLINGER_PERIOD - 1) is None


def test_compute_bollinger_band_at_excludes_current_candle():
    # 20 bougies plates à 100 puis une bougie extrême — la bande testant
    # cette dernière bougie doit rester centrée sur 100 (jamais influencée
    # par la bougie qu'elle sert à tester).
    candles = [_c(i, 101, 99, 100.0) for i in range(BOLLINGER_PERIOD)] + [_c(BOLLINGER_PERIOD, 500, 500, 500)]
    band = compute_bollinger_band_at(candles, BOLLINGER_PERIOD)
    assert band is not None
    upper, middle, lower = band
    assert middle == pytest.approx(100.0)
    assert upper == pytest.approx(100.0)  # écart-type nul sur une série plate


def test_compute_normalized_width_series_length_matches_candles():
    candles = _compression_breakout_series()
    assert len(compute_normalized_width_series(candles)) == len(candles)


def test_percentile_matches_known_values():
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0) == pytest.approx(1.0)
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 100) == pytest.approx(5.0)
    assert _percentile([42.0], 50) == pytest.approx(42.0)


def test_is_compressed_false_when_reference_window_insufficient():
    widths = [None] * 10 + [0.01] * COMPRESSION_DURATION
    assert _is_compressed(widths, len(widths), COMPRESSION_DURATION, COMPRESSION_PERCENTILE, PERCENTILE_LOOKBACK) is False


def test_is_compressed_false_when_compression_window_has_none():
    widths = [0.05] * PERCENTILE_LOOKBACK + [None] + [0.01] * (COMPRESSION_DURATION - 1)
    assert _is_compressed(widths, len(widths), COMPRESSION_DURATION, COMPRESSION_PERCENTILE, PERCENTILE_LOOKBACK) is False


# ---------------------------------------------------------------------------
# evaluate_entry — bout en bout
# ---------------------------------------------------------------------------

def test_evaluate_entry_long_breakout_after_compression():
    candles = _compression_breakout_series("long")
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == candles[-1].close
    assert signal.tp1 is None and signal.tp2 is None  # 100% trailing, aucune sortie partielle


def test_evaluate_entry_short_breakout_after_compression():
    candles = _compression_breakout_series("short")
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price > signal.entry_price


def test_evaluate_entry_stop_placed_inside_compression_zone():
    candles = _compression_breakout_series("long")
    signal = evaluate_entry("EURUSD", candles)
    band = compute_bollinger_band_at(candles, len(candles) - 1)
    upper, _middle, lower = band
    assert lower < signal.stop_price <= upper  # à l'intérieur de la zone, jamais au-delà


def test_evaluate_entry_none_when_compressed_but_no_breakout():
    candles = _compression_breakout_series("long", breakout=False)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_none_when_no_compression():
    # Amplitude alternée basse/haute dans la référence (distribution
    # avec un vrai étalement, pas une constante triviale) puis 10
    # dernières bougies à amplitude HAUTE (pas de resserrement récent) :
    # la bougie finale peut casser une bande, mais la compression exigée
    # n'a jamais eu lieu juste avant.
    candles, t = [], 0
    for block in range(12):
        amp = 1.0 if block % 2 == 0 else 5.0
        for i in range(10):
            price = 100 + (amp if i % 2 == 0 else -amp)
            candles.append(_c(t, price + 1, price - 1, price)); t += 1
    for i in range(10):
        price = 100 + (5.0 if i % 2 == 0 else -5.0)
        candles.append(_c(t, price + 1, price - 1, price)); t += 1
    candles.append(_c(t, 200, 50, 190))
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_none_on_too_short_series():
    assert evaluate_entry("EURUSD", [_c(0, 101, 99, 100)]) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", []) is None
    assert evaluate_entry("EURUSD", None) is None


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal():
    class _Broken:
        def __len__(self):
            return 500

        def __getitem__(self, item):
            raise RuntimeError("boom")

    assert evaluate_entry("EURUSD", _Broken()) is None


def test_module_constants_within_preregistered_grid():
    assert COMPRESSION_PERCENTILE in (10, 20, 30)
    assert COMPRESSION_DURATION in (5, 10, 15)
    assert STOP_BUFFER_PCT in (0.0, 0.25, 0.5)
    assert BOLLINGER_PERIOD == 20


def test_min_lookback_for_grid_covers_widest_grid_value():
    from src.hypothesis5_strategy_v2 import MIN_LOOKBACK_FOR_GRID, PERCENTILE_LOOKBACK
    widest_compression_duration = 15
    assert MIN_LOOKBACK_FOR_GRID >= BOLLINGER_PERIOD + widest_compression_duration + PERCENTILE_LOOKBACK
