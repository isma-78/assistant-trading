"""
hypothesis5_strategy_v2.py — Hypothèse #5, refonte L5 (29/08/2026,
pré-enregistré dans docs/HYPOTHESES.md AVANT tout calcul). Compression
→ expansion : largeur de Bollinger(20, 2σ) normalisée sous son
`COMPRESSION_PERCENTILE`-ième percentile pendant `COMPRESSION_DURATION`
bougies consécutives, puis cassure de la bande — direction = sens de la
cassure, stop à l'intérieur de la zone de compression. Remplace
`hypothesis5_strategy.py` (V3 : régime structurel + RSI(14)/50, archivé
— voir archive/). Sortie **100% trailing** (Donchian(20), FIGÉ, aucune
sortie partielle — décision explicite d'Ismaël, non balayée).

Choix théorique FIGÉ (invariant #10, justifié avant tout calcul) :
largeur de Bollinger plutôt qu'ATR brut pour la mesure de compression —
seule mesure normalisée relative au prix, comparable entre actifs
d'échelles très différentes (FX vs crypto), contrairement à l'ATR brut.
Bandes calculées en EXCLUANT la bougie courante (même convention que
`trend_strategy.compute_donchian_channel` — la bande doit précéder le
prix testé pour la cassure, sinon la bougie courante se compare à
elle-même et ne peut jamais casser sa propre bande).

Test d'information préalable (point 7 du pré-enregistrement, obligatoire
AVANT toute construction au-delà de la définition ci-dessus) : voir
`scripts/_h5_information_test.py` et docs/DECISIONS.md — corrélation
entre le sens de la cassure initiale et le sens du mouvement final, sur
la fenêtre de découverte, aucun seuil.
"""

from typing import List, Optional, Tuple

from src.market_data import Candle
from src.trend_strategy import TrendSignal, compute_donchian_channel

BOLLINGER_PERIOD = 20  # FIGÉ
BOLLINGER_STD_MULTIPLIER = 2.0  # FIGÉ
DONCHIAN_TRAILING_PERIOD = 20  # FIGÉ — sortie 100% trailing, aucune sortie partielle
PERCENTILE_LOOKBACK = 100  # FIGÉ — profondeur de la distribution de référence de largeur (mécanisme, pas une variable de grille)

# Variables AJUSTÉES (pré-enregistrement 29/08/2026, docs/HYPOTHESES.md) —
# valeurs par défaut = point médian de la grille, remplacées par
# hypothesis_params.apply_overrides une fois la calibration validée.
COMPRESSION_PERCENTILE = 20
COMPRESSION_DURATION = 10
STOP_BUFFER_PCT = 0.25

OVERRIDABLE = ["COMPRESSION_PERCENTILE", "COMPRESSION_DURATION", "STOP_BUFFER_PCT"]

# `MIN_LOOKBACK_FOR_GRID` (29/08/2026, voir docs/DECISIONS.md, point 16) :
# c'est exactement `min_required` calculé en interne par `_evaluate_
# entry` (BOLLINGER_PERIOD=20 + COMPRESSION_DURATION grille max 15 +
# PERCENTILE_LOOKBACK=100 = 135), avec la même marge de 20 que les
# autres hypothèses — la plus exigeante des 4, cohérent avec le
# pré-enregistrement (compression sur une distribution de référence
# longue).
MIN_LOOKBACK_FOR_GRID = 155


def compute_bollinger_band_at(
    candles: List[Candle], index: int, period: int = BOLLINGER_PERIOD, std_multiplier: float = BOLLINGER_STD_MULTIPLIER,
) -> Optional[Tuple[float, float, float]]:
    """Bandes de Bollinger (haute, médiane, basse) utilisables pour tester
    la bougie `index` — calculées sur les `period` bougies PRÉCÉDENTES,
    EXCLUANT `candles[index]` (même convention que `compute_donchian_
    channel` : la bande doit précéder le prix testé, sinon il se compare
    à lui-même). Écart-type de POPULATION. None si historique insuffisant."""
    start = index - period
    if start < 0:
        return None
    window = [c.close for c in candles[start:index]]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    std = variance ** 0.5
    return middle + std_multiplier * std, middle, middle - std_multiplier * std


def compute_normalized_width_series(candles: List[Candle], period: int = BOLLINGER_PERIOD) -> List[Optional[float]]:
    """Largeur de Bollinger normalisée `(haute-basse)/médiane` à chaque
    index (`None` tant que l'historique est insuffisant), même
    convention causale que `compute_bollinger_band_at`."""
    result: List[Optional[float]] = [None] * len(candles)
    for i in range(len(candles)):
        band = compute_bollinger_band_at(candles, i, period)
        if band is None:
            continue
        upper, middle, lower = band
        if middle != 0:
            result[i] = (upper - lower) / middle
    return result


def _percentile(values: List[float], pct: float) -> float:
    """Percentile par interpolation linéaire (méthode standard) sur une
    liste déjà non vide."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower_idx = int(rank)
    upper_idx = min(lower_idx + 1, len(ordered) - 1)
    frac = rank - lower_idx
    return ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx])


def _is_compressed(
    width_series: List[Optional[float]], breakout_index: int, duration: int, percentile: float, lookback: int,
) -> bool:
    """True si les `duration` bougies précédant `breakout_index` ont
    TOUTES une largeur sous le `percentile`-ième de la distribution de
    référence (les `lookback` bougies précédant CETTE fenêtre de
    compression, jamais la fenêtre elle-même — évite toute
    circularité)."""
    compression_window = width_series[breakout_index - duration:breakout_index]
    reference_window = width_series[breakout_index - duration - lookback:breakout_index - duration]
    if len(compression_window) < duration or any(v is None for v in compression_window):
        return False
    if len(reference_window) < lookback or any(v is None for v in reference_window):
        return False
    threshold = _percentile(reference_window, percentile)
    return all(v <= threshold for v in compression_window)


def evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    """Point d'entrée unique : cassure de la bande de Bollinger après une
    compression confirmée. Sortie 100% trailing (aucun tp1/tp2, géré par
    `compute_donchian_channel`/le mécanisme de trailing existant,
    jamais par ce module). Ne lève jamais d'exception (fail-safe,
    invariant #7, même patron que trend_strategy.evaluate_entry)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    if not candles:
        return None
    last_index = len(candles) - 1
    min_required = BOLLINGER_PERIOD + COMPRESSION_DURATION + PERCENTILE_LOOKBACK
    if last_index < min_required:
        return None

    # `min_required` (ci-dessus) est toujours >= BOLLINGER_PERIOD, donc
    # la bande est toujours calculable ici — pas de vérification
    # redondante sur une branche inatteignable (même constat que L1/L3).
    upper, _middle, lower = compute_bollinger_band_at(candles, last_index)

    width_series = compute_normalized_width_series(candles, BOLLINGER_PERIOD)
    if not _is_compressed(width_series, last_index, COMPRESSION_DURATION, COMPRESSION_PERCENTILE, PERCENTILE_LOOKBACK):
        return None

    close = candles[-1].close
    if close > upper:
        direction = "long"
        stop_price = upper - STOP_BUFFER_PCT * (upper - lower)
    elif close < lower:
        direction = "short"
        stop_price = lower + STOP_BUFFER_PCT * (upper - lower)
    else:
        return None  # compression confirmée, mais pas encore de cassure

    return TrendSignal(asset=asset, direction=direction, entry_price=close, stop_price=stop_price)
