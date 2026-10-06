"""
hypothesis2_strategy_v3cand.py — Candidate V2 pour l'Hypothèse #2
(06/10/2026, pré-enregistrée dans `docs/PROTOCOLE_EVOLUTION_V2_06-10.md`
AVANT tout calcul — reprend `docs/EVOLUTIONS_CANDIDATES.md`, 05/10/2026).

**Statut : candidate en test (walk-forward + shadow), JAMAIS déployée.
`hypothesis2_strategy_v2.py` n'est pas modifié et continue de tourner
tel quel.**

Signal-événement : la confluence multi-TF de L2/v2 est un ÉTAT vrai dans
98% des heures (diagnostic du 26/09/2026) — le contenu informationnel
d'une confluence n'existe qu'à sa FORMATION. Cette candidate n'entre que
sur la transition « non aligné → aligné » survenue dans les
`TRANSITION_LOOKBACK_CANDLES` dernières bougies de la résolution native.

Réutilise `hypothesis2_strategy_v2.evaluate_entry`/`compute_tf_vote` SANS
LES MODIFIER (le signal de base et son stop restent calculés à
l'identique — seul un filtre supplémentaire est appliqué par-dessus).

Anti-lookahead : le contrôle « était-ce déjà aligné à `t-k` ? » retrouve
l'état des timeframes de confirmation (H1/H4) TELS QU'ILS ÉTAIENT à
l'instant `t-k` — les séries H1/H4 fournies (déjà closes jusqu'à
l'instant `t`) sont tronquées à `time_utc <= temps de la bougie t-k`
avant d'être passées à `compute_tf_vote`, jamais la fenêtre complète
(qui inclurait des bougies H1/H4 closes APRÈS `t-k` mais avant `t`,
inconnues à `t-k`). Voir
`tests/test_hypothesis2_strategy_v3cand.py` pour la preuve directe
(un vote H1/H4 qui ne change de signe qu'ENTRE `t-k` et `t` ne doit
jamais faire paraître `t-k` comme déjà aligné).
"""

from typing import List, Optional

from src.hypothesis2_strategy_v2 import (
    EMA_PERIOD,
    N_TF,
    RSI_THRESHOLD,
    SCORE_THRESHOLD,
    compute_tf_vote,
    evaluate_entry as _v2_evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal

# Variable AJUSTÉE de cette candidate (budget 4/5 -> 5/5, plafond atteint,
# voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §1/§2). Grille figée à 6
# valeurs maximum, déclarée AVANT tout calcul de walk-forward :
TRANSITION_LOOKBACK_CANDLES_GRID = [1, 2, 3, 4, 6, 8]
TRANSITION_LOOKBACK_CANDLES = 3  # défaut = valeur médiane déclarée, jamais utilisée pour la sélection

OVERRIDABLE = ["TRANSITION_LOOKBACK_CANDLES"]  # candidate seule — n'étend jamais hypothesis2_strategy_v2.OVERRIDABLE


def _truncate_by_time(candles: List[Candle], cutoff_time_utc: str) -> List[Candle]:
    """Bougies dont `time_utc` <= `cutoff_time_utc` seulement — jamais une
    bougie close après ce repère temporel (anti-lookahead explicite pour
    le contrôle de transition ci-dessous)."""
    return [c for c in candles if c.time_utc <= cutoff_time_utc]


def was_already_aligned(
    candles_m15: List[Candle], candles_h1: List[Candle], candles_h4: List[Candle],
    direction: str, check_index: int,
) -> Optional[bool]:
    """Vrai si la confluence votait déjà `direction` (>= N_TF des 3 unités
    de temps, majoritaire) à l'index `check_index` de la série native —
    recalculée avec les fenêtres H1/H4 telles qu'elles étaient à cet
    instant (troncature par horodatage). `None` si l'historique H1/H4 est
    insuffisant à cet instant (impossible de trancher)."""
    if check_index < 0 or check_index >= len(candles_m15):
        return None
    cutoff = candles_m15[check_index].time_utc
    h1_then = _truncate_by_time(candles_h1, cutoff)
    h4_then = _truncate_by_time(candles_h4, cutoff)
    if not h1_then or not h4_then:
        return None

    votes = [
        compute_tf_vote(candles_m15[:check_index + 1], check_index),
        compute_tf_vote(h1_then, len(h1_then) - 1),
        compute_tf_vote(h4_then, len(h4_then) - 1),
    ]
    long_count, short_count = votes.count("long"), votes.count("short")
    if direction == "long":
        return long_count >= N_TF and long_count > short_count
    return short_count >= N_TF and short_count > long_count


def evaluate_entry(
    asset: str,
    candles_m15: Optional[List[Candle]],
    candles_h1: Optional[List[Candle]],
    candles_h4: Optional[List[Candle]],
) -> Optional[TrendSignal]:
    """Fail-safe (invariant #7) : ne lève jamais d'exception."""
    try:
        return _evaluate_entry(asset, candles_m15, candles_h1, candles_h4)
    except Exception:
        return None


def _evaluate_entry(
    asset: str,
    candles_m15: Optional[List[Candle]],
    candles_h1: Optional[List[Candle]],
    candles_h4: Optional[List[Candle]],
) -> Optional[TrendSignal]:
    base_signal = _v2_evaluate_entry(asset, candles_m15, candles_h1, candles_h4)
    if base_signal is None:
        return None

    check_index = len(candles_m15) - 1 - TRANSITION_LOOKBACK_CANDLES
    if check_index < 0:
        return None  # historique insuffisant pour juger de la transition (fail-safe)

    already_aligned = was_already_aligned(candles_m15, candles_h1, candles_h4, base_signal.direction, check_index)
    if already_aligned is None or already_aligned:
        return None  # état déjà établi (ou indéterminable) -> pas une transition nouvelle

    return base_signal
