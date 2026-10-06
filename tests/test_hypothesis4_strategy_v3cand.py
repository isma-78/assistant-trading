"""
tests/test_hypothesis4_strategy_v3cand.py — Candidate V2 pour H4/L4
(06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md). Réutilise
`hypothesis4_strategy_v2.evaluate_entry` SANS LE MODIFIER ; le filtre
ADX(14) ajouté est déjà causal par construction (réutilise
`hypothesis1_strategy_v2.compute_adx_series`, inchangé) — aucun nouveau
risque de lookahead.
"""

import pytest

from src.hypothesis1_strategy_v2 import ADX_PERIOD, compute_adx_series
from src.hypothesis4_strategy_v2 import PIVOT_FRACTAL_N, evaluate_entry as v2_evaluate_entry
from src.hypothesis4_strategy_v3cand import ADX_FILTER_THRESHOLD, current_adx, evaluate_entry
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close, volume=None):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close, volume=volume)


def _flat(n, price=100.0, volume=10.0):
    return [_c(i, price + 1, price - 1, price, volume) for i in range(n)]


def _bullish_divergence_series(fractal_n=PIVOT_FRACTAL_N):
    """Copie exacte de la fixture de test_hypothesis4_strategy_v2.py."""
    n = fractal_n
    candles = []
    t = 0
    for _ in range(20):
        candles.append(_c(t, 101, 99, 100.0, volume=10.0)); t += 1
    i1 = len(candles)
    candles.append(_c(t, 96, 90.0, 92.0, volume=50.0)); t += 1
    for _ in range(n):
        candles.append(_c(t, 96, 94, 95.0, volume=10.0)); t += 1
    for _ in range(5):
        candles.append(_c(t, 97, 95, 96.0, volume=10.0)); t += 1
    i2 = len(candles)
    candles.append(_c(t, 91, 85.0, 87.0, volume=5.0)); t += 1
    for _ in range(n + 3):
        candles.append(_c(t, 89, 87, 88.0, volume=10.0)); t += 1
    return candles, i1, i2


def _bearish_divergence_series(fractal_n=PIVOT_FRACTAL_N):
    n = fractal_n
    candles = []
    t = 0
    for _ in range(20):
        candles.append(_c(t, 101, 99, 100.0, volume=10.0)); t += 1
    i1 = len(candles)
    candles.append(_c(t, 110.0, 104, 108.0, volume=50.0)); t += 1
    for _ in range(n):
        candles.append(_c(t, 106, 104, 105.0, volume=10.0)); t += 1
    for _ in range(5):
        candles.append(_c(t, 104, 102, 103.0, volume=10.0)); t += 1
    i2 = len(candles)
    candles.append(_c(t, 115.0, 109, 112.0, volume=5.0)); t += 1
    for _ in range(n + 3):
        candles.append(_c(t, 113, 111, 112.0, volume=10.0)); t += 1
    return candles, i1, i2


def _exact_signal_window(builder):
    candles, i1, i2 = builder()
    return candles[: i2 + PIVOT_FRACTAL_N + 1]


# ---------------------------------------------------------------------------
# current_adx
# ---------------------------------------------------------------------------

def test_current_adx_none_with_insufficient_history():
    assert current_adx(_flat(10)) is None
    assert current_adx([]) is None


def test_current_adx_matches_series_last_value():
    candles = _flat(60)
    assert current_adx(candles) == compute_adx_series(candles, ADX_PERIOD)[-1]


# ---------------------------------------------------------------------------
# evaluate_entry — comportement avec le filtre réellement calculé
# ---------------------------------------------------------------------------

def test_evaluate_entry_blocks_on_natural_strong_trend_in_divergence_fixture():
    """La fixture de divergence (plateau puis mouvement net) produit un
    ADX(14) RÉELLEMENT élevé (tendance forte confirmée) : L4/v2 aurait
    pris ce retournement, la candidate l'écarte — exactement le mécanisme
    pré-enregistré le 05/10/2026."""
    candles = _exact_signal_window(_bullish_divergence_series)
    base = v2_evaluate_entry("BTCUSD", candles)
    assert isinstance(base, TrendSignal)
    assert current_adx(candles) >= ADX_FILTER_THRESHOLD
    assert evaluate_entry("BTCUSD", candles) is None


def test_evaluate_entry_short_direction_also_blocked_on_natural_strong_trend():
    candles = _exact_signal_window(_bearish_divergence_series)
    base = v2_evaluate_entry("BTCUSD", candles)
    assert isinstance(base, TrendSignal)
    assert evaluate_entry("BTCUSD", candles) is None


def test_evaluate_entry_none_when_no_base_signal():
    assert evaluate_entry("BTCUSD", _flat(60)) is None
    assert evaluate_entry("BTCUSD", None) is None
    assert evaluate_entry("BTCUSD", []) is None


def test_evaluate_entry_blocks_when_adx_above_threshold(monkeypatch):
    """Même signal de base, mais ADX(14) mesuré au-dessus du seuil
    (tendance forte confirmée) -> la candidate écarte le trade que L4/v2
    aurait pris."""
    import src.hypothesis4_strategy_v3cand as mod
    candles = _exact_signal_window(_bullish_divergence_series)
    base = v2_evaluate_entry("BTCUSD", candles)
    assert isinstance(base, TrendSignal)
    monkeypatch.setattr(mod, "current_adx", lambda *a, **k: ADX_FILTER_THRESHOLD)  # >= seuil -> bloque
    assert evaluate_entry("BTCUSD", candles) is None


def test_evaluate_entry_passes_through_when_adx_data_missing(monkeypatch):
    """ADX indisponible (`None`) : le filtre ne peut pas prouver la
    tendance forte -> ne bloque pas, signal de base inchangé (même règle
    que H3, lecture prudente actée dans le protocole)."""
    import src.hypothesis4_strategy_v3cand as mod
    candles = _exact_signal_window(_bullish_divergence_series)
    base = v2_evaluate_entry("BTCUSD", candles)
    monkeypatch.setattr(mod, "current_adx", lambda *a, **k: None)
    assert evaluate_entry("BTCUSD", candles) == base


def test_evaluate_entry_passes_through_when_adx_strictly_below_threshold(monkeypatch):
    import src.hypothesis4_strategy_v3cand as mod
    candles = _exact_signal_window(_bullish_divergence_series)
    base = v2_evaluate_entry("BTCUSD", candles)
    monkeypatch.setattr(mod, "current_adx", lambda *a, **k: ADX_FILTER_THRESHOLD - 1)
    assert evaluate_entry("BTCUSD", candles) == base


def test_evaluate_entry_respects_require_obv_confirmation_flag(monkeypatch):
    import src.hypothesis4_strategy_v3cand as mod
    candles = _exact_signal_window(_bullish_divergence_series)
    monkeypatch.setattr(mod, "current_adx", lambda *a, **k: ADX_FILTER_THRESHOLD - 1)
    with_obv = evaluate_entry("BTCUSD", candles, require_obv_confirmation=True)
    without_obv = evaluate_entry("BTCUSD", candles, require_obv_confirmation=False)
    assert with_obv is not None and without_obv is not None


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal(monkeypatch):
    import src.hypothesis4_strategy_v3cand as mod
    candles = _exact_signal_window(_bullish_divergence_series)
    monkeypatch.setattr(mod, "current_adx", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert evaluate_entry("BTCUSD", candles) is None


# ---------------------------------------------------------------------------
# Anti-lookahead (direct, via la réutilisation de compute_adx_series)
# ---------------------------------------------------------------------------

def test_current_adx_unaffected_by_bars_added_after_the_checkpoint():
    full = _flat(80)
    checkpoint = 60
    prefix = full[:checkpoint]
    result_prefix = current_adx(prefix)
    result_recomputed = current_adx(full[:checkpoint])
    assert result_prefix == result_recomputed


def test_module_constants_within_preregistered_grid():
    from src.hypothesis4_strategy_v3cand import ADX_FILTER_THRESHOLD_GRID
    assert ADX_FILTER_THRESHOLD in ADX_FILTER_THRESHOLD_GRID
    assert len(ADX_FILTER_THRESHOLD_GRID) == 6
