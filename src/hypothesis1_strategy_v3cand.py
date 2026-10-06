"""
hypothesis1_strategy_v3cand.py — Candidate V2 pour l'Hypothèse #1
(06/10/2026, pré-enregistrée dans `docs/PROTOCOLE_EVOLUTION_V2_06-10.md`
AVANT tout calcul — reprend `docs/EVOLUTIONS_CANDIDATES.md`, 05/10/2026).

**Statut : candidate en test (walk-forward + shadow), JAMAIS déployée sur
le compte réel/démo de L1. `hypothesis1_strategy_v2.py` n'est pas modifié
et continue de tourner tel quel.**

Entrée sur REPRISE après le croisement ADX, au lieu du croisement
lui-même (L1/v2) : l'ADX de Wilder est lissé et retardé, son
franchissement de seuil survient souvent en fin d'impulsion — attendre
la première clôture qui dépasse le plus haut/bas de la bougie de
croisement, dans une fenêtre fixe, filtre les croisements d'épuisement.

Réutilise `compute_adx_series`/`_ma_slope_direction` de
`hypothesis1_strategy_v2.py` SANS LES MODIFIER (même convention que
`hypothesis3_strategy_v2.py` import de `ict_strategy._find_regime_and_leg`,
ou `hypothesis2_strategy_v2.py` import de `hypothesis4_strategy_v2.
compute_rsi_series` — réutilisation de fonctions internes entre modules
de ce projet déjà établie).

Anti-lookahead : le croisement recherché à l'index `i` n'utilise jamais
une bougie postérieure à `i` (la pente à `i` est calculée avec
`candles[:i+1]` seulement, jamais la liste complète) ; la confirmation de
reprise ne regarde que la DERNIÈRE bougie fournie. Voir
`tests/test_hypothesis1_strategy_v3cand.py` pour la preuve directe.
"""

from typing import List, Optional

from src.hypothesis1_strategy_v2 import (
    ADX_PERIOD,
    ADX_THRESHOLD,
    ATR_PERIOD,
    K_ATR,
    MA_PERIOD,
    SLOPE_LOOKBACK,
    TP1_R_MULTIPLE,
    TP2_R_MULTIPLE,
    _ma_slope_direction,
    compute_adx_series,
)
from src.market_data import Candle, compute_atr
from src.trend_strategy import TrendSignal, compute_tp_levels

# Variable AJUSTÉE de cette candidate (budget 3/5 -> 4/5, voir
# docs/PROTOCOLE_EVOLUTION_V2_06-10.md §1/§2). Grille figée à 6 valeurs
# maximum, déclarée AVANT tout calcul de walk-forward :
ADX_RESUMPTION_WINDOW_CANDLES_GRID = [2, 3, 5, 8, 12, 18]
ADX_RESUMPTION_WINDOW_CANDLES = 5  # défaut = valeur médiane déclarée, jamais utilisée pour la sélection

OVERRIDABLE = ["ADX_RESUMPTION_WINDOW_CANDLES"]  # candidate seule — n'étend jamais hypothesis1_strategy_v2.OVERRIDABLE


def find_recent_confirmed_crossing(
    candles: List[Candle], direction: str, max_lookback_candles: int,
) -> Optional[int]:
    """Index (depuis le début de `candles`) du croisement ADX ascendant le
    plus récent, dans les `max_lookback_candles` dernières bougies, dont la
    pente de MA à CET index confirme `direction`. None si aucun.

    Causal par construction : `compute_adx_series(candles, ...)` ne
    produit, à chaque index, qu'une valeur dépendant des bougies jusqu'à
    cet index (déjà garanti par `hypothesis1_strategy_v2`, inchangé) ;
    `_ma_slope_direction(candles[:idx + 1], ...)` ne voit jamais au-delà
    de `idx`."""
    n = len(candles)
    if n < 2:
        return None
    adx_series = compute_adx_series(candles, ADX_PERIOD)
    # `earliest >= 1` toujours (le `max(1, ...)` ci-dessous) : `idx - 1`
    # n'est donc jamais négatif dans la boucle — pas de garde redondante
    # sur une branche inatteignable (même convention que
    # hypothesis1_strategy_v2.compute_adx_series).
    earliest = max(1, n - 1 - max_lookback_candles)
    for idx in range(n - 1, earliest - 1, -1):
        adx_now, adx_prev = adx_series[idx], adx_series[idx - 1]
        if adx_now is None or adx_prev is None:
            continue
        if adx_prev <= ADX_THRESHOLD < adx_now:
            slope_at_crossing = _ma_slope_direction(candles[:idx + 1], MA_PERIOD, SLOPE_LOOKBACK)
            if slope_at_crossing == direction:
                return idx
    return None


def evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    """Fail-safe (invariant #7, même patron que les autres `evaluate_entry`
    du projet) : ne lève jamais d'exception."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    if not candles or len(candles) < 2:
        return None

    direction = _ma_slope_direction(candles, MA_PERIOD, SLOPE_LOOKBACK)
    if direction is None:
        return None

    crossing_idx = find_recent_confirmed_crossing(candles, direction, ADX_RESUMPTION_WINDOW_CANDLES)
    if crossing_idx is None:
        return None

    crossing_bar = candles[crossing_idx]
    current_close = candles[-1].close
    resumed = current_close > crossing_bar.high if direction == "long" else current_close < crossing_bar.low
    if not resumed:
        return None

    atr = compute_atr(candles, ATR_PERIOD)
    if atr is None:
        return None  # historique insuffisant pour l'ATR malgré le croisement trouvé (fenêtre très courte)

    entry_price = current_close
    stop_price = entry_price - K_ATR * atr if direction == "long" else entry_price + K_ATR * atr
    tp1, tp2 = compute_tp_levels(direction, entry_price, stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE)
    return TrendSignal(asset=asset, direction=direction, entry_price=entry_price, stop_price=stop_price, tp1=tp1, tp2=tp2)
