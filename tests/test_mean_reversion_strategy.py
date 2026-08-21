"""
Tests unitaires de mean_reversion_strategy — module critique (§2.11,
§4.4 du CDC), 100% de couverture exigée (même règle que
trend_strategy.py/ict_strategy.py, demande explicite d'Ismaël pour
l'Hypothèse #4 de docs/HYPOTHESES.md — VALIDÉE en démo le 21/08/2026).

Les bandes attendues sont recalculées ici via `statistics.pstdev`
(bibliothèque standard, chemin de calcul indépendant du code testé)
plutôt que par des valeurs codées en dur à la main, pour éviter toute
erreur d'arithmétique de test tout en gardant une vérification
véritablement indépendante de l'implémentation.
"""

import statistics

import pytest

from src.market_data import Candle
from src.mean_reversion_strategy import (
    BOLLINGER_PERIOD,
    BOLLINGER_STD_MULTIPLIER,
    MA_PERIOD,
    STOP_WIDTH_MULTIPLIER,
    compute_bollinger_bands,
    evaluate_entry,
)


def _candle(close):
    return Candle(time_utc="t", open=close, high=close, low=close, close=close)


def _flat_candles(closes):
    return [_candle(c) for c in closes]


def _expected_bands(closes, multiplier=BOLLINGER_STD_MULTIPLIER):
    mean = statistics.mean(closes)
    std = statistics.pstdev(closes)
    return mean + multiplier * std, mean, mean - multiplier * std


# --- compute_bollinger_bands --------------------------------------------

def test_compute_bollinger_bands_none_when_not_enough_candles():
    candles = _flat_candles([100.0] * (BOLLINGER_PERIOD - 1))
    assert compute_bollinger_bands(candles) is None


def test_compute_bollinger_bands_flat_window_zero_std():
    candles = _flat_candles([150.0] * BOLLINGER_PERIOD)
    upper, middle, lower = compute_bollinger_bands(candles)
    assert upper == middle == lower == 150.0


def test_compute_bollinger_bands_matches_independent_population_stdev():
    # Fenêtre non triviale (10 x 190, 9 x 210, 1 x 200) : vérifie que
    # l'écart-type utilisé est bien celui de POPULATION (division par
    # BOLLINGER_PERIOD), pas celui d'échantillon (division par N-1).
    closes = [190.0] * 10 + [210.0] * 9 + [200.0]
    candles = _flat_candles(closes)
    upper, middle, lower = compute_bollinger_bands(candles)
    expected_upper, expected_middle, expected_lower = _expected_bands(closes)
    assert upper == pytest.approx(expected_upper)
    assert middle == pytest.approx(expected_middle)
    assert lower == pytest.approx(expected_lower)
    # Écart-type d'échantillon donnerait un résultat visiblement différent —
    # confirme qu'on n'a pas accidentellement utilisé stdev() au lieu de pstdev().
    sample_std = statistics.stdev(closes)
    assert (middle + BOLLINGER_STD_MULTIPLIER * sample_std) != pytest.approx(upper)


def test_compute_bollinger_bands_includes_current_candle():
    # 19 bougies à 100 + 1 bougie courante à 300 : si la bougie courante
    # était exclue (comme compute_donchian_channel), les bandes seraient
    # plates à 100 (std=0). Ici elle doit peser dans le calcul.
    candles = _flat_candles([100.0] * (BOLLINGER_PERIOD - 1) + [300.0])
    upper, middle, lower = compute_bollinger_bands(candles)
    assert middle != 100.0
    assert upper > 100.0


def test_compute_bollinger_bands_custom_period_and_multiplier():
    closes = [100.0, 100.0, 100.0, 200.0]
    upper, middle, lower = compute_bollinger_bands(_flat_candles(closes), period=4, std_multiplier=1.0)
    expected_upper, expected_middle, expected_lower = _expected_bands(closes, multiplier=1.0)
    assert upper == pytest.approx(expected_upper)
    assert middle == pytest.approx(expected_middle)
    assert lower == pytest.approx(expected_lower)


# --- evaluate_entry -------------------------------------------------------

def _base(value, count):
    return _flat_candles([value] * count)


def _window_touch_long():
    # 19 bougies à 200 + 1 bougie courante à 150 : la bougie courante
    # domine la variance (seule à s'écarter des 19 autres), donc elle
    # franchit systématiquement la bande basse (voir docs/HYPOTHESES.md
    # pour la propriété : dans une fenêtre "19 plates + 1 courante", la
    # bougie courante s'écarte toujours de plus de 2 sigma dès qu'elle
    # diffère des 19 autres).
    return _flat_candles([200.0] * (BOLLINGER_PERIOD - 1) + [150.0])


def _window_touch_short():
    return _flat_candles([150.0] * (BOLLINGER_PERIOD - 1) + [200.0])


def _window_no_touch():
    # 10 x 190, 9 x 210, courante à 200 : la variance vient des AUTRES
    # bougies, pas de la courante -> la courante (200) reste strictement
    # à l'intérieur des bandes (voir test_compute_bollinger_bands_matches_*).
    return _flat_candles([190.0] * 10 + [210.0] * 9 + [200.0])


def test_evaluate_entry_no_regime_returns_none():
    candles = _flat_candles([100.0] * MA_PERIOD)  # égalité stricte -> pas de régime
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_no_bands_returns_none():
    # Régime calculable (200 bougies) mais moins de BOLLINGER_PERIOD dans
    # l'historique n'est pas atteignable avec MA_PERIOD > BOLLINGER_PERIOD ;
    # branche défensive testée en isolant compute_bollinger_bands, même
    # patron que trend_strategy.test_evaluate_entry_no_channel_returns_none.
    from unittest.mock import patch

    candles = _flat_candles([100.0] * (MA_PERIOD - 1) + [200.0])
    with patch("src.mean_reversion_strategy.compute_bollinger_bands", return_value=None):
        assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_zero_band_width_returns_none():
    # Régime long (base à 100, courante à 200) mais fenêtre Bollinger
    # totalement plate (std=0, band_width=0) -> aucune extension
    # possible à détecter, pas de signal (même si current == bandes).
    candles = _base(100.0, MA_PERIOD - BOLLINGER_PERIOD) + _flat_candles([200.0] * BOLLINGER_PERIOD)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_long_touch_produces_signal():
    candles = _base(100.0, MA_PERIOD - BOLLINGER_PERIOD) + _window_touch_long()
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.asset == "EURUSD"
    assert signal.entry_price == 150.0
    assert signal.confidence == 1.0

    window_closes = [200.0] * (BOLLINGER_PERIOD - 1) + [150.0]
    upper, middle, lower = _expected_bands(window_closes)
    assert signal.take_profit == pytest.approx(middle)
    assert signal.stop_price == pytest.approx(150.0 - STOP_WIDTH_MULTIPLIER * (upper - lower) / 2)
    assert signal.stop_price < signal.entry_price  # stop en dessous pour un long


def test_evaluate_entry_long_no_touch_returns_none():
    candles = _base(100.0, MA_PERIOD - BOLLINGER_PERIOD) + _window_no_touch()
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_short_touch_produces_signal():
    candles = _base(300.0, MA_PERIOD - BOLLINGER_PERIOD) + _window_touch_short()
    signal = evaluate_entry("GBPUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 200.0

    window_closes = [150.0] * (BOLLINGER_PERIOD - 1) + [200.0]
    upper, middle, lower = _expected_bands(window_closes)
    assert signal.take_profit == pytest.approx(middle)
    assert signal.stop_price == pytest.approx(200.0 + STOP_WIDTH_MULTIPLIER * (upper - lower) / 2)
    assert signal.stop_price > signal.entry_price  # stop au-dessus pour un short


def test_evaluate_entry_short_no_touch_returns_none():
    candles = _base(300.0, MA_PERIOD - BOLLINGER_PERIOD) + _window_no_touch()
    assert evaluate_entry("GBPUSD", candles) is None


def test_evaluate_entry_internal_error_is_caught_fail_safe():
    assert evaluate_entry("EURUSD", None) is None
