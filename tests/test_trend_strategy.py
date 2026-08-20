"""
Tests unitaires de trend_strategy — module critique (§2.11, §4.4 du CDC),
100% de couverture exigée (même règle que risk_engine.py, demande
explicite d'Ismaël pour l'Hypothèse #1 de docs/HYPOTHESES.md).
"""

from unittest.mock import patch

import pytest

from src.market_data import Candle
from src.trend_strategy import (
    DONCHIAN_PERIOD,
    MA_PERIOD,
    compute_donchian_channel,
    compute_regime,
    compute_trailing_stop_channel,
    evaluate_entry,
)


def _candle(close, high=None, low=None):
    high = high if high is not None else close
    low = low if low is not None else close
    return Candle(time_utc="t", open=close, high=high, low=low, close=close)


def _flat_candles(closes):
    return [_candle(c) for c in closes]


# --- compute_regime ---------------------------------------------------

def test_compute_regime_none_when_not_enough_candles():
    candles = _flat_candles([100.0] * (MA_PERIOD - 1))
    assert compute_regime(candles) is None


def test_compute_regime_long_when_above_ma():
    # 199 bougies à 100, dernière à 200 -> MA200 ~ 100.5, clôture 200 > MA
    candles = _flat_candles([100.0] * (MA_PERIOD - 1) + [200.0])
    assert compute_regime(candles) == "long"


def test_compute_regime_short_when_below_ma():
    candles = _flat_candles([100.0] * (MA_PERIOD - 1) + [50.0])
    assert compute_regime(candles) == "short"


def test_compute_regime_none_on_exact_equality():
    # Toutes les bougies identiques -> MA == clôture courante exactement
    candles = _flat_candles([100.0] * MA_PERIOD)
    assert compute_regime(candles) is None


# --- compute_donchian_channel ------------------------------------------

def test_compute_donchian_channel_none_when_not_enough_candles():
    candles = _flat_candles([100.0] * DONCHIAN_PERIOD)  # besoin de period+1
    assert compute_donchian_channel(candles) is None


def test_compute_donchian_channel_excludes_current_candle():
    # 20 bougies formant le canal (high=110, low=90), + 1 bougie courante
    # avec un high/low extrême qui NE DOIT PAS compter dans le canal.
    window = [_candle(100.0, high=110.0, low=90.0) for _ in range(DONCHIAN_PERIOD)]
    current = _candle(100.0, high=999.0, low=1.0)
    candles = window + [current]
    highest, lowest = compute_donchian_channel(candles)
    assert highest == 110.0
    assert lowest == 90.0


def test_compute_donchian_channel_custom_period():
    candles = _flat_candles([100.0, 105.0, 95.0, 102.0])
    highest, lowest = compute_donchian_channel(candles, period=3)
    # Fenêtre = 3 premières bougies (100, 105, 95), la 4e est la bougie courante exclue
    assert highest == 105.0
    assert lowest == 95.0


# --- evaluate_entry -----------------------------------------------------

def _regime_setup(direction: str, breakout: bool):
    """Construit un jeu de bougies avec un régime MA(200) donné, un canal
    de Donchian(20) stable, et une bougie courante qui casse (ou pas) le
    canal dans le sens attendu."""
    if direction == "long":
        base = [_candle(100.0, high=105.0, low=95.0) for _ in range(MA_PERIOD - DONCHIAN_PERIOD - 1)]
        channel = [_candle(100.0, high=105.0, low=95.0) for _ in range(DONCHIAN_PERIOD)]
        current_close = 110.0 if breakout else 102.0  # 105 = plus haut du canal
        current = _candle(current_close)
        return base + channel + [current]
    else:
        base = [_candle(100.0, high=105.0, low=95.0) for _ in range(MA_PERIOD - DONCHIAN_PERIOD - 1)]
        channel = [_candle(100.0, high=105.0, low=95.0) for _ in range(DONCHIAN_PERIOD)]
        current_close = 85.0 if breakout else 98.0  # 95 = plus bas du canal
        current = _candle(current_close)
        return base + channel + [current]


def test_evaluate_entry_no_regime_returns_none():
    candles = _flat_candles([100.0] * MA_PERIOD)  # égalité stricte -> pas de régime
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_no_channel_returns_none():
    # MA_PERIOD (200) > DONCHIAN_PERIOD+1 (21) : dans les faits, dès que le
    # régime est calculable, le canal l'est aussi — cette branche est un
    # garde-fou défensif qui ne peut pas être atteint via de vraies
    # bougies avec les constantes actuelles. Testée en isolant l'appel
    # (compute_donchian_channel forcé à None) plutôt qu'en essayant de
    # construire un historique impossible.
    candles = _flat_candles([100.0] * (MA_PERIOD - 1) + [200.0])
    with patch("src.trend_strategy.compute_donchian_channel", return_value=None):
        assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_long_breakout_produces_signal():
    candles = _regime_setup("long", breakout=True)
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 110.0
    assert signal.stop_price == 95.0  # plus bas du canal
    assert signal.confidence == 1.0
    assert signal.asset == "EURUSD"


def test_evaluate_entry_long_no_breakout_returns_none():
    candles = _regime_setup("long", breakout=False)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_short_breakout_produces_signal():
    candles = _regime_setup("short", breakout=True)
    signal = evaluate_entry("GBPUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 85.0
    assert signal.stop_price == 105.0  # plus haut du canal


def test_evaluate_entry_short_no_breakout_returns_none():
    candles = _regime_setup("short", breakout=False)
    assert evaluate_entry("GBPUSD", candles) is None


def test_evaluate_entry_internal_error_is_caught_fail_safe():
    # candles=None fait planter len(candles) à l'intérieur -> capturé
    assert evaluate_entry("EURUSD", None) is None


# --- compute_trailing_stop_channel --------------------------------------

def test_trailing_channel_long_tightens_stop_up():
    # Canal (hors bougie courante) : low=95, stop actuel encore à 90
    # (placé plus large à l'origine) -> resserré à 95.
    candles = _flat_candles([100.0] * DONCHIAN_PERIOD) + [_candle(102.0)]
    new_stop = compute_trailing_stop_channel("long", candles, current_stop=90.0)
    assert new_stop == 100.0  # low du canal (bougies plates à 100)


def test_trailing_channel_short_tightens_stop_down():
    candles = _flat_candles([100.0] * DONCHIAN_PERIOD) + [_candle(98.0)]
    new_stop = compute_trailing_stop_channel("short", candles, current_stop=110.0)
    assert new_stop == 100.0  # high du canal


def test_trailing_channel_long_never_widens():
    # Canal donnerait low=95 (plus bas que le stop actuel de 97) ->
    # candidat moins favorable, le stop reste à 97 (invariant #5).
    window = [_candle(100.0, high=105.0, low=95.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_candle(102.0)]
    new_stop = compute_trailing_stop_channel("long", candles, current_stop=97.0)
    assert new_stop == 97.0


def test_trailing_channel_short_never_widens():
    window = [_candle(100.0, high=105.0, low=95.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_candle(98.0)]
    new_stop = compute_trailing_stop_channel("short", candles, current_stop=103.0)
    assert new_stop == 103.0


def test_trailing_channel_insufficient_history_returns_unchanged():
    candles = _flat_candles([100.0] * (DONCHIAN_PERIOD - 1))
    assert compute_trailing_stop_channel("long", candles, current_stop=90.0) == 90.0


def test_trailing_channel_unknown_direction_raises():
    candles = _flat_candles([100.0] * (DONCHIAN_PERIOD + 1))
    with pytest.raises(ValueError):
        compute_trailing_stop_channel("sideways", candles, current_stop=90.0)
