"""
mean_reversion_strategy.py — Stratégie technique complémentaire. MODULE
CRITIQUE (§2.11, §4.4 du CDC : même exigence de couverture que
trend_strategy.py/ict_strategy.py — demande explicite d'Ismaël pour ce
palier).

Implémente l'Hypothèse #4 de docs/HYPOTHESES.md, VALIDÉE par Ismaël le
21/08/2026 (autorisée en démo, aucun capital réel — voir docs/DECISIONS.md
pour la décision sur le plafond §3.9 : toute future évaluation/validation
d'une hypothèse appliquera désormais la correction pour comparaisons
multiples calibrée sur 4 hypothèses, pas 3) : filtre de régime MA(200)
horaire (réutilisé tel quel depuis trend_strategy.py, jamais recalculé
différemment) + déclencheur de retour à la moyenne sur Bandes de
Bollinger(20, 2σ) horaires. On ne fade jamais contre le régime de fond,
seulement les extensions à court terme à l'intérieur du régime autorisé.

Deux choix de calcul non spécifiés par la proposition initiale,
tranchés par Ismaël le 21/08/2026 (voir docs/HYPOTHESES.md/DECISIONS.md) :
- "Largeur de bande" pour le stop = DEMI-écart (médiane -> bande = 2σ),
  pas l'écart complet (4σ) retenu par erreur dans la première version.
  Voir STOP_WIDTH_MULTIPLIER — stop et cible désormais symétriques
  (R:R ≈ 1:1), au lieu du 2:1 défavorable de la version initiale.
- Écart-type de POPULATION (division par BOLLINGER_PERIOD), pas
  d'échantillon — convention Bollinger standard, confirmé sans changement.

Contrairement à trend_strategy.py (Hypothèse #1) : sortie par
take-profit fixe (bande médiane figée à l'entrée), stop fixe, AUCUN
trailing (conforme invariant #5 : un stop qui ne bouge jamais ne peut
jamais être élargi). MeanReversionSignal porte donc un champ
take_profit que TrendSignal n'a pas — la gestion de position
correspondante (TP unique, fermeture à 100%, pas de trailing) ne
s'intègre pas dans l'un des deux patrons existants de
executor._evaluate_position_management ; voir docs/HYPOTHESES.md pour
le détail de cet écart, non implémenté ici (module de signal/sizing
seul, pas de câblage d'exécution).

Aucun LLM (invariant #1) : comparaisons numériques déterministes,
fail-safe (invariant #7 — toute erreur interne devient "pas de signal",
jamais un signal potentiellement invalide).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.market_data import Candle
from src.trend_strategy import MA_PERIOD, compute_regime

BOLLINGER_PERIOD = 20      # Paramètre #1 (avec le multiplicateur d'écart-type) — voir docs/HYPOTHESES.md
BOLLINGER_STD_MULTIPLIER = 2.0  # Paramètre #1 (couplé à BOLLINGER_PERIOD, jamais ajusté séparément)

# Paramètre #2 — décision d'Ismaël (21/08/2026, voir docs/HYPOTHESES.md et
# docs/DECISIONS.md) : appliqué à la DEMI-largeur de bande (médiane -> bande,
# soit 1 écart-type × BOLLINGER_STD_MULTIPLIER = 2σ), PAS la largeur complète
# (haute − basse = 4σ) retenue par erreur dans la proposition initiale. Avec
# TP à la bande médiane (2σ de la clôture d'entrée) et ce choix, stop et
# cible sont symétriques : R:R ≈ 1:1, au lieu du 2:1 défavorable de la
# première version (stop deux fois plus large que la cible).
STOP_WIDTH_MULTIPLIER = 1.0

__all__ = [
    "MA_PERIOD",
    "BOLLINGER_PERIOD",
    "BOLLINGER_STD_MULTIPLIER",
    "STOP_WIDTH_MULTIPLIER",
    "MeanReversionSignal",
    "compute_bollinger_bands",
    "evaluate_entry",
]


@dataclass(frozen=True)
class MeanReversionSignal:
    asset: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_price: float
    take_profit: float
    confidence: float = 1.0  # déterministe par construction — voir docs/HYPOTHESES.md


def compute_bollinger_bands(
    candles: List[Candle], period: int = BOLLINGER_PERIOD, std_multiplier: float = BOLLINGER_STD_MULTIPLIER,
) -> Optional[Tuple[float, float, float]]:
    """Bandes de Bollinger sur `period` bougies, INCLUANT la bougie
    courante (candles[-1]) — même convention que trend_strategy.
    compute_regime (la MA de régime inclut aussi la bougie courante),
    volontairement distincte de compute_donchian_channel qui exclut la
    bougie courante (le canal de rupture doit précéder le prix testé ;
    ici la bande elle-même est le repère testé, elle doit donc inclure
    le point testé, comme toute implémentation standard des Bandes de
    Bollinger).

    Retourne (bande_haute, bande_médiane, bande_basse), écart-type de
    POPULATION (division par `period`, pas `period - 1` — voir
    docs/HYPOTHESES.md). None si pas assez d'historique."""
    if len(candles) < period:
        return None
    window = [c.close for c in candles[-period:]]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    std = variance ** 0.5
    upper = middle + std_multiplier * std
    lower = middle - std_multiplier * std
    return upper, middle, lower


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[MeanReversionSignal]:
    """Point d'entrée unique (§2.11) : régime MA(200) PUIS toucher de la
    bande de Bollinger opposée au régime (retour à la moyenne, jamais
    contre le régime). TP = bande médiane au moment de l'entrée, figée.
    Stop = entrée ∓ STOP_WIDTH_MULTIPLIER × DEMI-largeur de la bande
    (médiane -> bande, décision d'Ismaël du 21/08/2026 — voir
    docs/HYPOTHESES.md) : stop et cible symétriques par construction
    (R:R ≈ 1:1 avec STOP_WIDTH_MULTIPLIER=1.0).

    Ne lève jamais d'exception : toute erreur interne devient "pas de
    signal" plutôt qu'un signal potentiellement invalide (fail-safe,
    invariant #7 — même patron que trend_strategy/ict_strategy)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[MeanReversionSignal]:
    regime = compute_regime(candles)
    if regime is None:
        return None

    bands = compute_bollinger_bands(candles)
    if bands is None:
        return None
    upper, middle, lower = bands
    half_width = (upper - lower) / 2  # médiane -> bande = 1 écart-type × BOLLINGER_STD_MULTIPLIER
    if half_width <= 0:
        return None

    current_close = candles[-1].close

    if regime == "long" and current_close <= lower:
        stop_price = current_close - STOP_WIDTH_MULTIPLIER * half_width
        return MeanReversionSignal(
            asset=asset, direction="long", entry_price=current_close,
            stop_price=stop_price, take_profit=middle,
        )

    if regime == "short" and current_close >= upper:
        stop_price = current_close + STOP_WIDTH_MULTIPLIER * half_width
        return MeanReversionSignal(
            asset=asset, direction="short", entry_price=current_close,
            stop_price=stop_price, take_profit=middle,
        )

    return None
