"""
tests/test_hypothesis1_strategy_v2.py — H1/L1 (refonte 29/08/2026, voir
docs/HYPOTHESES.md).
"""

import pytest

from src.hypothesis1_strategy_v2 import (
    ADX_PERIOD,
    ADX_THRESHOLD,
    K_ATR,
    MA_PERIOD,
    SLOPE_LOOKBACK,
    _ma_slope_direction,
    compute_adx_series,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close)


def _choppy_then_trend(chop_len, trend_len, start=100.0, chop_step=0.1, trend_step=0.5):
    """Construit une série choppy (ADX bas) suivie d'une tendance nette
    (ADX monte progressivement) — même construction vérifiée
    numériquement pendant le développement (voir docs/DECISIONS.md)."""
    candles = []
    price = start
    t = 0
    for i in range(chop_len):
        price += chop_step if i % 2 == 0 else -chop_step
        candles.append(_c(t, price + 0.3, price - 0.3, price)); t += 1
    for _ in range(trend_len):
        price += trend_step
        candles.append(_c(t, price + 0.3, price - 0.3, price)); t += 1
    return candles


def _find_upward_crossing(series, threshold):
    for i in range(1, len(series)):
        if series[i - 1] is not None and series[i] is not None and series[i - 1] <= threshold < series[i]:
            return i
    return None


# ---------------------------------------------------------------------------
# compute_adx_series
# ---------------------------------------------------------------------------

def test_compute_adx_series_none_before_enough_history():
    candles = _choppy_then_trend(10, 0)
    assert all(v is None for v in compute_adx_series(candles, ADX_PERIOD))


def test_compute_adx_series_bounded_between_0_and_100():
    candles = _choppy_then_trend(MA_PERIOD + 40, 40)
    values = [v for v in compute_adx_series(candles, ADX_PERIOD) if v is not None]
    assert values
    assert all(0.0 <= v <= 100.0 for v in values)


def test_compute_adx_series_rises_during_clean_trend():
    candles = _choppy_then_trend(MA_PERIOD + 40, 40)
    adx = compute_adx_series(candles, ADX_PERIOD)
    # une tendance nette et prolongée doit finir par pousser l'ADX vers le haut
    assert adx[-1] > adx[len(candles) - 40]


def test_compute_adx_series_length_matches_candles():
    candles = _choppy_then_trend(MA_PERIOD + 40, 40)
    assert len(compute_adx_series(candles, ADX_PERIOD)) == len(candles)


def test_compute_adx_series_zero_when_flat_series_zero_true_range():
    # Bougies parfaitement identiques (TR/+DM/-DM nuls partout) -> DX/ADX
    # défini à 0, pas une division par zéro.
    candles = [_c(i, 100.0, 100.0, 100.0) for i in range(2 * ADX_PERIOD + 5)]
    values = [v for v in compute_adx_series(candles, ADX_PERIOD) if v is not None]
    assert values
    assert all(v == 0.0 for v in values)


# ---------------------------------------------------------------------------
# _ma_slope_direction
# ---------------------------------------------------------------------------

def test_ma_slope_direction_long_on_rising_ma():
    candles = [_c(i, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1) for i in range(MA_PERIOD + SLOPE_LOOKBACK + 5)]
    assert _ma_slope_direction(candles, MA_PERIOD, SLOPE_LOOKBACK) == "long"


def test_ma_slope_direction_short_on_falling_ma():
    candles = [_c(i, 101 - i * 0.1, 99 - i * 0.1, 100 - i * 0.1) for i in range(MA_PERIOD + SLOPE_LOOKBACK + 5)]
    assert _ma_slope_direction(candles, MA_PERIOD, SLOPE_LOOKBACK) == "short"


def test_ma_slope_direction_none_when_insufficient_history():
    candles = [_c(i, 101, 99, 100) for i in range(MA_PERIOD)]
    assert _ma_slope_direction(candles, MA_PERIOD, SLOPE_LOOKBACK) is None


def test_ma_slope_direction_none_when_flat():
    candles = [_c(i, 101, 99, 100.0) for i in range(MA_PERIOD + SLOPE_LOOKBACK + 5)]
    assert _ma_slope_direction(candles, MA_PERIOD, SLOPE_LOOKBACK) is None


# ---------------------------------------------------------------------------
# evaluate_entry — bout en bout
# ---------------------------------------------------------------------------

def test_evaluate_entry_long_exactly_on_adx_upward_crossing():
    candles = _choppy_then_trend(MA_PERIOD + 40, 40)
    adx = compute_adx_series(candles, ADX_PERIOD)
    cross_idx = _find_upward_crossing(adx, ADX_THRESHOLD)
    assert cross_idx is not None
    at_cross = candles[: cross_idx + 1]

    signal = evaluate_entry("EURUSD", at_cross)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == at_cross[-1].close
    assert signal.stop_price == pytest.approx(signal.entry_price - K_ATR * _atr_at(at_cross))
    assert signal.tp1 is not None and signal.tp2 is not None


def _atr_at(candles):
    from src.market_data import compute_atr
    from src.hypothesis1_strategy_v2 import ATR_PERIOD
    return compute_atr(candles, ATR_PERIOD)


def test_evaluate_entry_short_exactly_on_adx_upward_crossing_with_falling_ma():
    candles = _choppy_then_trend(MA_PERIOD + 40, 40, chop_step=0.1, trend_step=-0.5)
    adx = compute_adx_series(candles, ADX_PERIOD)
    cross_idx = _find_upward_crossing(adx, ADX_THRESHOLD)
    assert cross_idx is not None
    at_cross = candles[: cross_idx + 1]

    signal = evaluate_entry("EURUSD", at_cross)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price > signal.entry_price


def test_evaluate_entry_none_one_bar_before_crossing():
    candles = _choppy_then_trend(MA_PERIOD + 40, 40)
    adx = compute_adx_series(candles, ADX_PERIOD)
    cross_idx = _find_upward_crossing(adx, ADX_THRESHOLD)
    before_cross = candles[:cross_idx]
    assert evaluate_entry("EURUSD", before_cross) is None


def test_evaluate_entry_none_when_adx_already_established_above_threshold():
    # Une bougie APRÈS le franchissement : ADX déjà au-dessus, ce n'est
    # plus un franchissement -> pas de nouveau signal (événement
    # ponctuel, jamais un état persistant).
    candles = _choppy_then_trend(MA_PERIOD + 40, 40)
    adx = compute_adx_series(candles, ADX_PERIOD)
    cross_idx = _find_upward_crossing(adx, ADX_THRESHOLD)
    one_more = candles[: cross_idx + 2]
    assert evaluate_entry("EURUSD", one_more) is None


def test_evaluate_entry_none_when_no_regime_direction():
    candles = [_c(i, 101, 99, 100.0) for i in range(MA_PERIOD + 60)]  # plat : ni pente ni ADX exploitable
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_none_on_too_short_series():
    assert evaluate_entry("EURUSD", [_c(0, 101, 99, 100)]) is None


def test_evaluate_entry_none_when_adx_history_insufficient_but_slope_available():
    # Assez de bougies pour une pente de MA (si MA_PERIOD était petit)
    # mais pas pour ADX(14) (exige 2*14+1=29) — ici on force une pente
    # définie sur une fenêtre courte en abaissant artificiellement le
    # nombre de bougies sous le seuil ADX tout en gardant une tendance
    # nette, pour isoler cette branche précise.
    candles = [_c(i, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1) for i in range(20)]
    from src.hypothesis1_strategy_v2 import _evaluate_entry
    import src.hypothesis1_strategy_v2 as mod
    old_ma_period = mod.MA_PERIOD
    mod.MA_PERIOD = 10
    try:
        assert _ma_slope_direction(candles, mod.MA_PERIOD, SLOPE_LOOKBACK) is not None
        assert _evaluate_entry("EURUSD", candles) is None  # ADX indisponible (20 < 29)
    finally:
        mod.MA_PERIOD = old_ma_period


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
    assert MA_PERIOD in (100, 150, 200, 250)
    assert ADX_THRESHOLD in (20, 25, 30)
    assert K_ATR in (1.5, 2.0, 2.5, 3.0)
    assert ADX_PERIOD == 14
