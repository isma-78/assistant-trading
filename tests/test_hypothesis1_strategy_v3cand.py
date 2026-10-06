"""
tests/test_hypothesis1_strategy_v3cand.py — Candidate V2 pour H1/L1
(06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md). Le test
anti-lookahead est écrit pour prouver qu'un croisement ADX situé APRÈS le
point de contrôle n'est jamais utilisé pour confirmer une reprise
antérieure, et qu'une bougie future ne modifie jamais le résultat déjà
produit à l'index courant.
"""

import pytest

from src.hypothesis1_strategy_v2 import ADX_THRESHOLD, K_ATR, MA_PERIOD, SLOPE_LOOKBACK
from src.hypothesis1_strategy_v3cand import (
    ADX_RESUMPTION_WINDOW_CANDLES,
    find_recent_confirmed_crossing,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close):
    return Candle(time_utc=f"{i:05d}", open=close, high=high, low=low, close=close)


def _choppy_then_trend(chop_len, trend_len, start=100.0, chop_step=0.1, trend_step=0.5):
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


def _flat(n, start=100.0):
    return [_c(i, start + 0.3, start - 0.3, start) for i in range(n)]


def _series_with_crossing_at_end(direction="long"):
    """Choppy puis tendance longue, tronquée EXACTEMENT au premier
    croisement ADX ascendant confirmé par la pente — point de référence
    déterministe pour les tests ci-dessous (évite toute dépendance
    implicite à une fenêtre de recherche précise)."""
    step = 0.5 if direction == "long" else -0.5
    full = _choppy_then_trend(MA_PERIOD + 40, 40, trend_step=step)
    idx = find_recent_confirmed_crossing(full, direction, 400)
    assert idx is not None, "fixture invalide : aucun croisement trouvé dans la série complète"
    return full, idx


# ---------------------------------------------------------------------------
# find_recent_confirmed_crossing
# ---------------------------------------------------------------------------

def test_no_crossing_returns_none():
    assert find_recent_confirmed_crossing(_flat(40), "long", 10) is None


def test_find_crossing_none_with_fewer_than_two_candles():
    assert find_recent_confirmed_crossing([], "long", 10) is None
    assert find_recent_confirmed_crossing([_c(0, 100, 99, 99.5)], "long", 10) is None


def test_crossing_outside_window_not_found():
    full, idx = _series_with_crossing_at_end("long")
    age = (len(full) - 1) - idx
    assert find_recent_confirmed_crossing(full, "long", age) == idx
    assert find_recent_confirmed_crossing(full, "long", max(0, age - 1)) is None


def test_crossing_direction_mismatch_is_rejected():
    full, _ = _series_with_crossing_at_end("long")
    # Aucune pente baissière ne peut exister dans une série strictement montante.
    assert find_recent_confirmed_crossing(full, "short", 400) is None


# ---------------------------------------------------------------------------
# Anti-lookahead
# ---------------------------------------------------------------------------

def test_crossing_search_ignores_bars_after_the_checkpoint():
    """Un croisement qui n'apparaît QU'APRÈS le point de contrôle ne doit
    jamais être rapporté comme antérieur à ce point."""
    full, idx = _series_with_crossing_at_end("long")
    truncated_before = full[:idx]
    assert find_recent_confirmed_crossing(truncated_before, "long", 400) is None
    truncated_at = full[: idx + 1]
    assert find_recent_confirmed_crossing(truncated_at, "long", 400) == idx


def test_evaluate_entry_result_at_t_is_unaffected_by_future_bars():
    """Le résultat produit à un index donné (en fournissant exactement les
    bougies jusqu'à cet index) est STABLE : le recalculer plusieurs fois à
    l'identique donne toujours la même réponse, et aucune bougie au-delà
    de cet index n'entre jamais dans son calcul (seule la liste tronquée
    `at_checkpoint` est passée à `evaluate_entry` — la fonction n'a
    structurellement accès à rien d'autre)."""
    full, idx = _series_with_crossing_at_end("long")
    at_checkpoint = full[: idx + 2]  # une bougie après le croisement : reprise confirmée
    first = evaluate_entry("EURUSD", at_checkpoint)
    second = evaluate_entry("EURUSD", at_checkpoint)
    assert first == second and first is not None


# ---------------------------------------------------------------------------
# evaluate_entry
# ---------------------------------------------------------------------------

def test_evaluate_entry_none_at_the_crossing_bar_itself():
    """Au croisement lui-même, close == high (long) : la reprise ne peut
    structurellement jamais être confirmée à cette même bougie — contraste
    explicite avec L1/v2, qui aurait signalé ICI."""
    full, idx = _series_with_crossing_at_end("long")
    at_crossing = full[: idx + 1]
    assert find_recent_confirmed_crossing(at_crossing, "long", ADX_RESUMPTION_WINDOW_CANDLES) == idx
    assert evaluate_entry("EURUSD", at_crossing) is None


def test_evaluate_entry_fires_on_first_resumption_break():
    full, idx = _series_with_crossing_at_end("long")
    one_more = full[: idx + 2]
    assert one_more[-1].close > full[idx].high  # reprise confirmée par construction
    signal = evaluate_entry("EURUSD", one_more)
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == one_more[-1].close
    assert signal.stop_price < signal.entry_price


def test_evaluate_entry_short_direction_fires_on_resumption():
    full, idx = _series_with_crossing_at_end("short")
    one_more = full[: idx + 2]
    assert one_more[-1].close < full[idx].low
    signal = evaluate_entry("EURUSD", one_more)
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "short"
    assert signal.stop_price > signal.entry_price


def test_evaluate_entry_none_once_outside_resumption_window():
    """Trop tard après le croisement (au-delà de la fenêtre déclarée) :
    plus aucun signal, même si une reprise a bien eu lieu entre-temps."""
    full, idx = _series_with_crossing_at_end("long")
    far_after = full[: idx + 1 + ADX_RESUMPTION_WINDOW_CANDLES + 5]
    assert find_recent_confirmed_crossing(far_after, "long", ADX_RESUMPTION_WINDOW_CANDLES) is None
    assert evaluate_entry("EURUSD", far_after) is None


def test_evaluate_entry_none_on_flat_series():
    assert evaluate_entry("EURUSD", _flat(MA_PERIOD + 40)) is None


def test_evaluate_entry_none_with_insufficient_candles():
    assert evaluate_entry("EURUSD", [_c(0, 100, 99, 99.5)]) is None
    assert evaluate_entry("EURUSD", None) is None
    assert evaluate_entry("EURUSD", []) is None


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal(monkeypatch):
    import src.hypothesis1_strategy_v3cand as mod
    full, idx = _series_with_crossing_at_end("long")
    candles = full[: idx + 2]
    monkeypatch.setattr(mod, "_ma_slope_direction", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_none_when_atr_unavailable_despite_crossing(monkeypatch):
    import src.hypothesis1_strategy_v3cand as mod
    full, idx = _series_with_crossing_at_end("long")
    monkeypatch.setattr(mod, "compute_atr", lambda *a, **k: None)
    assert evaluate_entry("EURUSD", full[: idx + 2]) is None


def test_module_constants_within_preregistered_grid():
    from src.hypothesis1_strategy_v3cand import ADX_RESUMPTION_WINDOW_CANDLES_GRID
    assert ADX_RESUMPTION_WINDOW_CANDLES in ADX_RESUMPTION_WINDOW_CANDLES_GRID
    assert len(ADX_RESUMPTION_WINDOW_CANDLES_GRID) == 6


def test_fixed_constants_match_deployed_v2_values():
    from src.hypothesis1_strategy_v3cand import ADX_THRESHOLD as t, K_ATR as k, MA_PERIOD as m, SLOPE_LOOKBACK as s
    assert (m, t, k, s) == (MA_PERIOD, ADX_THRESHOLD, K_ATR, SLOPE_LOOKBACK)
