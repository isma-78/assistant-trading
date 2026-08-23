"""
hypothesis2_strategy.py — Sortie à prise de profit pour l'Hypothèse #2,
décision EXPLICITE d'Ismaël le 23/08/2026 (voir docs/DECISIONS.md) : le
trailing Donchian(20) pur d'origine est remplacé par le mécanisme §2.10
(TP1 50% à 1R / TP2 30% à 2R / TP3 20% sous trailing 2×ATR(14), plancher
breakeven, stop au breakeven dès TP1 touché) — déjà câblé pour Station X
et l'Hypothèse #5, réutilisé ici SANS nouvelle logique de sortie.

Entrée (régime structurel BOS/CHoCH + confluence ICT — swings fractals
K=2, zone de Fibonacci, FVG) INCHANGÉE — réutilise `ict_strategy.
evaluate_entry` à l'identique, importée directement, jamais redupliquée.
Seul ce module ajoute TP1/TP2 par-dessus le signal reçu, via
`trend_strategy.compute_tp_levels` (partagée avec
hypothesis3_strategy.py — pure arithmétique de R-multiples, aucune
nouvelle logique de décision).

Prospectif uniquement (voir docs/DECISIONS.md) : ce module s'applique
aux signaux générés à partir de son déploiement — les trades H2 déjà
ouverts ou clos avant cette date gardent leur trailing pur d'origine
jusqu'à leur clôture normale, aucune position en cours n'est modifiée.

Aucun LLM (invariant #1) : traduction numérique déterministe, fail-safe
(invariant #7 — toute erreur interne devient "pas de signal", même
patron qu'ict_strategy.evaluate_entry)."""

from dataclasses import replace
from typing import List, Optional

from src.ict_strategy import evaluate_entry as _ict_evaluate_entry
from src.trend_strategy import TrendSignal, compute_tp_levels
from src.market_data import Candle

# Seuls paramètres propres à ce changement (§2.11, cap 2-3 — voir
# docs/HYPOTHESES.md) : distance de TP1/TP2 en multiples de R, mêmes
# valeurs que H3/H5 (même mécanisme §2.10, pas un second choix
# indépendant).
TP1_R_MULTIPLE = 1.0
TP2_R_MULTIPLE = 2.0


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    """Point d'entrée unique : délègue entièrement à `ict_strategy.
    evaluate_entry` (réutilisée à l'identique) puis calcule TP1(1R)/
    TP2(2R) sur le risque initial du signal obtenu. Ne lève jamais
    d'exception : toute erreur interne devient "pas de signal"
    (fail-safe, invariant #7)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    signal = _ict_evaluate_entry(asset, candles)
    if signal is None:
        return None
    tp1, tp2 = compute_tp_levels(
        signal.direction, signal.entry_price, signal.stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE,
    )
    return replace(signal, tp1=tp1, tp2=tp2)
