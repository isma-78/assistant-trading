"""
hypothesis4_strategy_v3cand.py — Candidate V2 pour l'Hypothèse #4
(06/10/2026, pré-enregistrée dans `docs/PROTOCOLE_EVOLUTION_V2_06-10.md`
AVANT tout calcul — reprend `docs/EVOLUTIONS_CANDIDATES.md`, 05/10/2026).

**Statut : candidate en test (walk-forward + shadow), JAMAIS déployée.
`hypothesis4_strategy_v2.py` n'est pas modifié et continue de tourner
tel quel. Règle du 25/09/2026 rappelée : jamais empilée sur E1/v2 avant
son propre verdict.**

Filtre de force de tendance : une divergence de retournement échoue plus
souvent en tendance forte. Retient le signal de divergence de L4/v2
(réutilisé sans modification) uniquement si ADX(14) < `ADX_FILTER_
THRESHOLD` à la bougie courante — même réutilisation inter-modules de
`compute_adx_series` que `hypothesis2_strategy_v2.py` réutilise déjà
`compute_rsi_series` de `hypothesis4_strategy_v2.py`.

Anti-lookahead : `compute_adx_series` (hypothesis1_strategy_v2, inchangé)
ne dépend, à chaque index, que des bougies jusqu'à cet index — jamais une
bougie future.
"""

from typing import List, Optional

from src.hypothesis1_strategy_v2 import ADX_PERIOD, compute_adx_series
from src.hypothesis4_strategy_v2 import evaluate_entry as _v2_evaluate_entry
from src.market_data import Candle
from src.trend_strategy import TrendSignal

# Variable AJUSTÉE de cette candidate (budget 3/5 -> 4/5). Grille figée à
# 6 valeurs maximum, déclarée AVANT tout calcul de walk-forward :
ADX_FILTER_THRESHOLD_GRID = [15, 20, 25, 30, 35, 40]
ADX_FILTER_THRESHOLD = 25  # défaut déclaré, jamais utilisé pour la sélection

OVERRIDABLE = ["ADX_FILTER_THRESHOLD"]  # candidate seule — n'étend jamais hypothesis4_strategy_v2.OVERRIDABLE


def current_adx(candles: List[Candle]) -> Optional[float]:
    """ADX(14) à la dernière bougie fournie, `None` si l'historique est
    insuffisant (29 bougies minimum, voir hypothesis1_strategy_v2)."""
    if not candles:
        return None
    return compute_adx_series(candles, ADX_PERIOD)[-1]


def evaluate_entry(
    asset: str, candles: Optional[List[Candle]], require_obv_confirmation: bool = True,
) -> Optional[TrendSignal]:
    """Fail-safe (invariant #7) : ne lève jamais d'exception. Signature
    identique à L4/v2 (`require_obv_confirmation` : paramètre diagnostique
    inchangé, jamais une variable de grille)."""
    try:
        return _evaluate_entry(asset, candles, require_obv_confirmation)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: Optional[List[Candle]], require_obv_confirmation: bool) -> Optional[TrendSignal]:
    base_signal = _v2_evaluate_entry(asset, candles, require_obv_confirmation)
    if base_signal is None:
        return None

    adx = current_adx(candles)
    if adx is not None and adx >= ADX_FILTER_THRESHOLD:
        return None  # tendance forte confirmée : divergence écartée (mécanisme identifié le 05/10)
    # adx is None (donnée manquante -> le filtre ne peut pas prouver la
    # tendance forte, il ne bloque donc pas, même règle que H3 — voir
    # docs/PROTOCOLE_EVOLUTION_V2_06-10.md §1, lecture prudente explicite)
    # ou adx < seuil : signal inchangé.
    return base_signal
