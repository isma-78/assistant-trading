"""
ARCHIVÉ le 29/08/2026 — remplacé par src/hypothesis3_strategy_v2.py
(pullback en tendance sur régime structurel réutilisé, L3) dans le
cadre de la refonte complète H1-H5 (voir docs/DECISIONS.md, entrée
"Hypothèse #3 — refonte L3" du 29/08/2026, et docs/HYPOTHESES.md,
pré-enregistrement du 29/08/2026). Conservé tel quel pour l'audit —
plus jamais importé par aucun executor. `trend_strategy.py` (dont ce
module dépendait) N'EST PAS archivé : ses utilitaires partagés
(TrendSignal/compute_tp_levels/etc.) restent actifs. 6 positions H3 v1
étaient réellement ouvertes au moment du remplacement (voir
docs/DECISIONS.md, avertissement de transition dans
hypothesis3_executor.py). Ne pas modifier ce fichier.

hypothesis3_strategy.py — Sortie à prise de profit pour l'Hypothèse #3,
décision EXPLICITE d'Ismaël le 23/08/2026 (voir docs/DECISIONS.md) : le
trailing Donchian(20) pur d'origine est remplacé par le mécanisme §2.10
(TP1 50% à 1R / TP2 30% à 2R / TP3 20% sous trailing 2×ATR(14), plancher
breakeven, stop au breakeven dès TP1 touché) — déjà câblé pour Station X
et l'Hypothèse #5, réutilisé ici SANS nouvelle logique de sortie.

Ce changement va délibérément à l'encontre de la recommandation de
préserver H3 comme copie exacte de H1 (isolation "timeframe seule" —
voir docs/HYPOTHESES.md, entrée du 20/08/2026, "identique à l'Hypothèse
#1, seule la résolution de bougie change"). Assumé et documenté par
Ismaël, pas une dérive silencieuse : H1 reste inchangée (voir
`trend_executor.py`, jamais touché), seul témoin encore en trailing pur.

Entrée (régime MA(200) + rupture de canal Donchian(20)) INCHANGÉE —
réutilise `trend_strategy.evaluate_entry` à l'identique, importée
directement, jamais redupliquée. Seul ce module ajoute TP1/TP2 par-dessus
le signal reçu, via `trend_strategy.compute_tp_levels` (partagée avec
hypothesis2_strategy.py — pure arithmétique de R-multiples, aucune
nouvelle logique de décision).

Prospectif uniquement (voir docs/DECISIONS.md) : ce module s'applique
aux signaux générés à partir de son déploiement — les trades H3 déjà
ouverts ou clos avant cette date gardent leur trailing pur d'origine
jusqu'à leur clôture normale, aucune position en cours n'est modifiée.

Aucun LLM (invariant #1) : traduction numérique déterministe, fail-safe
(invariant #7 — toute erreur interne devient "pas de signal", même
patron que trend_strategy.evaluate_entry)."""

from dataclasses import replace
from typing import List, Optional

from src.trend_strategy import TrendSignal, compute_tp_levels
from src.trend_strategy import evaluate_entry as _trend_evaluate_entry
from src.market_data import Candle

# Seuls paramètres propres à ce changement (§2.11, cap 2-3 — voir
# docs/HYPOTHESES.md) : distance de TP1/TP2 en multiples de R, valeurs
# rondes choisies a priori, identiques à celles déjà retenues pour H5
# (même mécanisme §2.10, même choix, pas un second choix indépendant).
TP1_R_MULTIPLE = 1.0
TP2_R_MULTIPLE = 2.0


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    """Point d'entrée unique : délègue entièrement à `trend_strategy.
    evaluate_entry` (réutilisée à l'identique) puis calcule TP1(1R)/
    TP2(2R) sur le risque initial du signal obtenu. Ne lève jamais
    d'exception : toute erreur interne devient "pas de signal"
    (fail-safe, invariant #7)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    signal = _trend_evaluate_entry(asset, candles)
    if signal is None:
        return None
    tp1, tp2 = compute_tp_levels(
        signal.direction, signal.entry_price, signal.stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE,
    )
    return replace(signal, tp1=tp1, tp2=tp2)
