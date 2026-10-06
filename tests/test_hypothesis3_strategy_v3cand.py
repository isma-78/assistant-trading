"""
tests/test_hypothesis3_strategy_v3cand.py — Candidate V2 pour H3/L3
(06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md). Réutilise
`hypothesis3_strategy_v2.evaluate_entry` SANS LE MODIFIER ; le filtre
ajouté (expansion de volatilité) ne dépend que de l'historique jusqu'à la
bougie courante (ATR, déjà causal, inchangé) — aucun nouveau risque de
lookahead introduit par ce module.
"""

import pytest

from src.hypothesis3_strategy_v2 import evaluate_entry as v2_evaluate_entry
from src.hypothesis3_strategy_v3cand import (
    ATR_SHORT_PERIOD,
    VOLATILITY_EXPANSION_RATIO,
    evaluate_entry,
    is_volatility_expanding,
)
from src.market_data import Candle, compute_atr
from src.trend_strategy import TrendSignal


def _c(i, h, l, c):
    return Candle(time_utc=str(i), open=c, high=h, low=l, close=c)


def _pullback_series(direction="long"):
    """Copie exacte de la fixture de test_hypothesis3_strategy_v2.py
    (régime + jambe + retracement + reprise, baseline plate 200 bougies
    -> ATR(14)/ATR(7) quasi nuls avant le repli, donc PAS d'expansion de
    volatilité par construction)."""
    baseline = [_c(i, 100, 100, 100) for i in range(200)]
    recent, t = [], 200
    for _ in range(3):
        recent.append(_c(t, 100, 100, 100)); t += 1
    if direction == "long":
        recent.append(_c(t, 92, 80, 85)); t += 1
        for _ in range(2):
            recent.append(_c(t, 100, 90, 95)); t += 1
        recent.append(_c(t, 140, 120, 135)); t += 1
        for _ in range(2):
            recent.append(_c(t, 120, 100, 110)); t += 1
        for _ in range(2):
            recent.append(_c(t, 125, 115, 120)); t += 1
        recent.append(_c(t, 115, 108, 110)); t += 1
        recent.append(_c(t, 115, 108, 108)); t += 1
        recent.append(_c(t, 130, 108, 125)); t += 1
        recent.append(_c(t, 150, 125, 145)); t += 1
    else:
        recent.append(_c(t, 120, 108, 115)); t += 1
        for _ in range(2):
            recent.append(_c(t, 110, 100, 105)); t += 1
        recent.append(_c(t, 80, 60, 65)); t += 1
        for _ in range(2):
            recent.append(_c(t, 100, 80, 90)); t += 1
        for _ in range(2):
            recent.append(_c(t, 85, 75, 80)); t += 1
        recent.append(_c(t, 92, 85, 90)); t += 1
        recent.append(_c(t, 92, 85, 92)); t += 1
        recent.append(_c(t, 92, 70, 75)); t += 1
        recent.append(_c(t, 70, 50, 55)); t += 1
    return baseline + recent


def _flat(n):
    return [_c(i, 100, 100, 100) for i in range(n)]


# ---------------------------------------------------------------------------
# is_volatility_expanding
# ---------------------------------------------------------------------------

def test_is_volatility_expanding_none_on_perfectly_flat_series():
    """ATR(7) et ATR(14) sont tous deux nuls (aucune variation) : le
    ratio est indéterminé, jamais une fausse "expansion"."""
    assert is_volatility_expanding(_flat(60)) is None


def test_is_volatility_expanding_false_on_steady_mild_volatility():
    """Amplitude constante sur toute la fenêtre : ATR(7) ≈ ATR(14),
    ratio ≈ 1 -> pas d'expansion."""
    steady = [_c(i, 101.0, 99.0, 100.0) for i in range(60)]
    assert is_volatility_expanding(steady, ratio_threshold=1.2) is False


def test_is_volatility_expanding_none_when_atr_unavailable():
    assert is_volatility_expanding(_flat(3)) is None


def test_is_volatility_expanding_true_when_short_atr_dominates():
    """Longue période calme, PUIS un sursaut récent d'amplitude : ATR(7)
    réagit plus vite qu'ATR(14), le ratio dépasse le seuil."""
    calm = [_c(i, 100.2, 99.8, 100.0) for i in range(60)]
    burst = [_c(60 + i, 110.0, 90.0, 100.0) for i in range(8)]
    candles = calm + burst
    assert is_volatility_expanding(candles, ratio_threshold=1.2) is True
    assert is_volatility_expanding(candles, ratio_threshold=50.0) is False


def test_is_volatility_expanding_only_uses_candles_up_to_the_last_one_provided():
    """Anti-lookahead direct : ajouter des bougies futures après le point
    d'évaluation ne doit jamais changer le résultat déjà obtenu à un point
    antérieur (même logique que compute_atr, déjà causal, réutilisé tel
    quel)."""
    calm = [_c(i, 100.2, 99.8, 100.0) for i in range(60)]
    result_short = is_volatility_expanding(calm)
    extended = calm + [_c(60 + i, 200.0, 50.0, 100.0) for i in range(5)]  # choc futur énorme
    result_recomputed_on_prefix = is_volatility_expanding(extended[:60])
    assert result_short == result_recomputed_on_prefix


# ---------------------------------------------------------------------------
# evaluate_entry
# ---------------------------------------------------------------------------

def test_evaluate_entry_blocks_on_natural_expansion_in_pullback_fixture():
    """La fixture de repli (jambe 80->140, swings larges après 200
    bougies plates) constitue elle-même une expansion de volatilité
    réelle (ATR(7) domine ATR(14)) : L3/v2 aurait pris ce trade, la
    candidate l'écarte — exactement le mécanisme pré-enregistré le
    05/10/2026."""
    candles = _pullback_series("long")
    base = v2_evaluate_entry("EURUSD", candles)
    assert isinstance(base, TrendSignal)
    assert is_volatility_expanding(candles, VOLATILITY_EXPANSION_RATIO) is True
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_short_direction_also_blocked_on_natural_expansion():
    candles = _pullback_series("short")
    base = v2_evaluate_entry("EURUSD", candles)
    assert isinstance(base, TrendSignal)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_none_when_no_base_signal():
    assert evaluate_entry("EURUSD", _flat(220)) is None


def test_evaluate_entry_passes_through_with_lenient_threshold(monkeypatch):
    """Même donnée, mais un seuil assez permissif pour ne jamais bloquer
    (grille à l'extrémité haute) : le signal de base passe inchangé,
    preuve que le filtre — et seulement lui — est responsable du blocage
    observé au test précédent."""
    import src.hypothesis3_strategy_v3cand as mod
    candles = _pullback_series("long")
    base = v2_evaluate_entry("EURUSD", candles)
    monkeypatch.setattr(mod, "VOLATILITY_EXPANSION_RATIO", 1000.0)
    assert evaluate_entry("EURUSD", candles) == base


def test_evaluate_entry_passes_through_when_volatility_data_missing(monkeypatch):
    """Donnée d'expansion indisponible (None) : le filtre ne peut pas
    prouver l'expansion -> ne bloque pas, signal de base inchangé."""
    import src.hypothesis3_strategy_v3cand as mod
    candles = _pullback_series("long")
    base = v2_evaluate_entry("EURUSD", candles)
    assert isinstance(base, TrendSignal)
    monkeypatch.setattr(mod, "is_volatility_expanding", lambda *a, **k: None)
    assert evaluate_entry("EURUSD", candles) == base


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal(monkeypatch):
    import src.hypothesis3_strategy_v3cand as mod
    candles = _pullback_series("long")
    monkeypatch.setattr(mod, "is_volatility_expanding", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert evaluate_entry("EURUSD", candles) is None


def test_module_constants_within_preregistered_grid():
    from src.hypothesis3_strategy_v3cand import VOLATILITY_EXPANSION_RATIO_GRID
    assert VOLATILITY_EXPANSION_RATIO in VOLATILITY_EXPANSION_RATIO_GRID
    assert len(VOLATILITY_EXPANSION_RATIO_GRID) == 6
    assert ATR_SHORT_PERIOD == 7
