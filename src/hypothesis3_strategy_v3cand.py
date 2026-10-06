"""
hypothesis3_strategy_v3cand.py — Candidate V2 pour l'Hypothèse #3
(06/10/2026, pré-enregistrée dans `docs/PROTOCOLE_EVOLUTION_V2_06-10.md`
AVANT tout calcul — reprend `docs/EVOLUTIONS_CANDIDATES.md`, 05/10/2026).

**Statut : candidate en test (walk-forward + shadow), JAMAIS déployée.
`hypothesis3_strategy_v2.py` n'est pas modifié et continue de tourner
tel quel.**

Pas de pullback quand la volatilité est en EXPANSION : un retracement
pendant une expansion de volatilité (ATR court > ATR long) a plus de
chances d'être un retournement qu'une pause de liquidité. Filtre
appliqué PAR-DESSUS le signal de base de L3/v2, réutilisé sans
modification (régime/jambe/retracement/confirmation inchangés).

Anti-lookahead : `compute_atr` (market_data, inchangé) ne dépend que des
bougies jusqu'à l'index courant — aucune bougie future n'est utilisée,
ni par le signal de base ni par ce filtre.
"""

from typing import List, Optional

from src.hypothesis3_strategy_v2 import evaluate_entry as _v2_evaluate_entry
from src.market_data import Candle, compute_atr
from src.trend_strategy import TrendSignal

# Constante FIGÉE, choisie a priori, JAMAIS balayée (ce n'est pas la
# variable ajustée de cette candidate — voir
# docs/PROTOCOLE_EVOLUTION_V2_06-10.md §1).
ATR_SHORT_PERIOD = 7

# Variable AJUSTÉE de cette candidate (budget 3/5 -> 4/5). Grille figée à
# 6 valeurs maximum, déclarée AVANT tout calcul de walk-forward :
VOLATILITY_EXPANSION_RATIO_GRID = [1.0, 1.1, 1.2, 1.3, 1.5, 2.0]
VOLATILITY_EXPANSION_RATIO = 1.2  # défaut déclaré, jamais utilisé pour la sélection

OVERRIDABLE = ["VOLATILITY_EXPANSION_RATIO"]  # candidate seule — n'étend jamais hypothesis3_strategy_v2.OVERRIDABLE


def is_volatility_expanding(candles: List[Candle], ratio_threshold: float = VOLATILITY_EXPANSION_RATIO) -> Optional[bool]:
    """Vrai si ATR(`ATR_SHORT_PERIOD`) / ATR(14) > `ratio_threshold` à la
    dernière bougie fournie. `None` si l'un des deux ATR est indisponible
    (donnée manquante — voir règle du module, le filtre ne bloque pas
    dans ce cas, décision prise au niveau de `evaluate_entry`)."""
    atr_short = compute_atr(candles, ATR_SHORT_PERIOD)
    atr_long = compute_atr(candles, 14)
    if atr_short is None or atr_long is None or atr_long == 0:
        return None
    return (atr_short / atr_long) > ratio_threshold


def evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    """Fail-safe (invariant #7) : ne lève jamais d'exception."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    base_signal = _v2_evaluate_entry(asset, candles)
    if base_signal is None:
        return None

    expanding = is_volatility_expanding(candles, VOLATILITY_EXPANSION_RATIO)
    if expanding is True:
        return None  # expansion détectée : retracement écarté (mécanisme de perte identifié le 05/10)
    # expanding is False, ou None (donnée manquante -> le filtre ne peut
    # pas prouver l'expansion, il ne bloque donc pas, voir docstring du
    # module et docs/PROTOCOLE_EVOLUTION_V2_06-10.md §1) : signal inchangé.
    return base_signal
