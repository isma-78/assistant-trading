"""
hypothesis2_strategy_v2.py — Hypothèse #2, refonte L2 (29/08/2026,
pré-enregistré dans docs/HYPOTHESES.md AVANT tout calcul). Confluence
multi-timeframe : alignement EMA/Ichimoku(9/26/52 FIGÉ)/RSI(14 FIGÉ)
sur `N_TF` des 3 unités de temps fixes (M15 native + H1 + H4). Remplace
l'ancien H2 (confluence ICT/Fibonacci/FVG, `ict_strategy.evaluate_entry`
+ `hypothesis2_strategy.py`, tous deux ARCHIVÉS — `ict_strategy.py`
lui-même reste actif, réutilisé par H3/L3 pour son régime structurel).

Ichimoku, risque de lookahead classique et sévère (identifié par
Ismaël avant construction) : senkou span A/B sont décalés de
`ICHIMOKU_SHIFT` (26) périodes VERS L'AVANT par construction — le
nuage utilisable à la bougie t est calculé à partir de t-26, jamais
plus tard. `compute_ichimoku_cloud_at(candles, index)` applique ce
décalage EXPLICITEMENT (`base_idx = index - shift`), jamais implicite
— voir tests/test_hypothesis2_strategy_v2.py, écrits AVANT ce fichier,
preuve directe que le résultat à un index donné est indépendant de
tout ce qui suit `index - shift`.

Multi-timeframe : ce module est SCINDÉ de son wiring live. La fonction
publique `evaluate_entry(asset, candles_m15, candles_h1, candles_h4)`
est pure (aucun accès réseau) — les 3 séries doivent être fournies par
l'appelant. Le wiring live (`hypothesis2_executor.py`) effectue deux
appels `get_candles` supplémentaires (H1/H4, même actif, faible
profondeur) avant de déléguer ici — écart architectural documenté dans
docs/DECISIONS.md (point C), le contrat générique `entry_fn(asset,
candles)` à une seule résolution de `technical_strategy_executor.
run_technical_strategy_loop` ne suffit pas à ce besoin spécifique.
"""

from typing import List, Optional, Tuple

from src.hypothesis4_strategy_v2 import compute_rsi_series
from src.market_data import Candle, compute_atr
from src.trend_strategy import TrendSignal, compute_donchian_channel, compute_tp_levels

RSI_PERIOD = 14  # FIGÉ
ICHIMOKU_TENKAN_PERIOD = 9   # FIGÉ (Hosoda)
ICHIMOKU_KIJUN_PERIOD = 26   # FIGÉ (Hosoda)
ICHIMOKU_SENKOU_B_PERIOD = 52  # FIGÉ (Hosoda)
ICHIMOKU_SHIFT = 26  # FIGÉ — décalage standard senkou, = période Kijun
STRUCTURE_LOOKBACK = 20  # FIGÉ — même période que trend_strategy.DONCHIAN_PERIOD (convention "structure" du
                         # projet) ; volontairement >= ATR_PERIOD+1 pour que le repli ATR soit atteignable
                         # tôt dans une série (sinon la structure serait toujours disponible avant l'ATR)
ATR_PERIOD = 14  # FIGÉ, utilisé seulement en repli si la structure est indisponible
ATR_FALLBACK_MULT = 1.0  # FIGÉ, repli non négocié (voir pré-enregistrement)
TP1_R_MULTIPLE = 1.0  # FIGÉ, structure de sortie standard §2.10
TP2_R_MULTIPLE = 2.0  # FIGÉ

# Variables AJUSTÉES (pré-enregistrement 29/08/2026, docs/HYPOTHESES.md) —
# valeurs par défaut = point médian de la grille, remplacées par
# hypothesis_params.apply_overrides une fois la calibration validée.
EMA_PERIOD = 50
RSI_THRESHOLD = 50.0
N_TF = 2
SCORE_THRESHOLD = 2 / 3  # corrigé le 29/08/2026 avant tout calcul (voir docs/HYPOTHESES.md) : 2 indicateurs sur 3

OVERRIDABLE = ["EMA_PERIOD", "RSI_THRESHOLD", "N_TF", "SCORE_THRESHOLD"]


def compute_ema(candles: List[Candle], period: int = EMA_PERIOD) -> Optional[float]:
    """EMA standard (amorcée par la SMA des `period` premières bougies,
    lissage exponentiel ensuite — définition manuel, pas une formule
    inventée pour ce projet). None si historique insuffisant."""
    if len(candles) < period:
        return None
    closes = [c.close for c in candles]
    ema = sum(closes[:period]) / period
    alpha = 2.0 / (period + 1)
    for close in closes[period:]:
        ema = alpha * close + (1 - alpha) * ema
    return ema


def _period_midpoint(candles: List[Candle], end_idx: int, period: int) -> Optional[float]:
    start = end_idx - period + 1
    if start < 0:
        return None
    window = candles[start:end_idx + 1]
    return (max(c.high for c in window) + min(c.low for c in window)) / 2.0


def compute_ichimoku_cloud_at(
    candles: List[Candle], index: int, shift: int = ICHIMOKU_SHIFT,
) -> Optional[Tuple[float, float]]:
    """Nuage (senkou_a, senkou_b) UTILISABLE à la bougie `index` — calculé
    à partir de `index - shift`, jamais depuis `index` lui-même (le
    nuage traditionnellement "tracé" à la position courante représente en
    réalité une valeur calculée `shift` bougies plus tôt). `shift=0`
    n'existe que pour le test de contrôle négatif (démonstration qu'une
    implémentation naïve sans décalage donnerait un résultat différent),
    jamais utilisé par `evaluate_entry`."""
    base_idx = index - shift
    if base_idx < 0:
        return None
    tenkan = _period_midpoint(candles, base_idx, ICHIMOKU_TENKAN_PERIOD)
    kijun = _period_midpoint(candles, base_idx, ICHIMOKU_KIJUN_PERIOD)
    if tenkan is None or kijun is None:
        return None
    senkou_b = _period_midpoint(candles, base_idx, ICHIMOKU_SENKOU_B_PERIOD)
    if senkou_b is None:
        return None
    senkou_a = (tenkan + kijun) / 2.0
    return senkou_a, senkou_b


def compute_tf_vote(candles: List[Candle], index: int) -> Optional[str]:
    """Direction "confirmée" (`SCORE_THRESHOLD` des 3 indicateurs
    d'accord) pour une unité de temps donnée, à l'index `index`. None si
    historique insuffisant pour un des 3 indicateurs, ou si aucune
    direction n'atteint le seuil de score."""
    if index < 0 or index >= len(candles):
        return None
    window = candles[:index + 1]
    ema = compute_ema(window, EMA_PERIOD)
    rsi_series = compute_rsi_series(window, RSI_PERIOD)
    rsi = rsi_series[index] if rsi_series else None
    cloud = compute_ichimoku_cloud_at(window, index)
    if ema is None or rsi is None or cloud is None:
        return None

    senkou_a, senkou_b = cloud
    cloud_top, cloud_bottom = max(senkou_a, senkou_b), min(senkou_a, senkou_b)
    price = candles[index].close

    long_score = sum([price > ema, rsi > RSI_THRESHOLD, price > cloud_top]) / 3.0
    short_score = sum([price < ema, rsi < (100.0 - RSI_THRESHOLD), price < cloud_bottom]) / 3.0

    if long_score >= SCORE_THRESHOLD and long_score > short_score:
        return "long"
    if short_score >= SCORE_THRESHOLD and short_score > long_score:
        return "short"
    return None


def evaluate_entry(
    asset: str,
    candles_m15: Optional[List[Candle]],
    candles_h1: Optional[List[Candle]],
    candles_h4: Optional[List[Candle]],
) -> Optional[TrendSignal]:
    """Point d'entrée unique : confluence sur `N_TF` des 3 unités de
    temps fixes (M15 native + H1 + H4, fournies par l'appelant — module
    pur, aucun accès réseau ici). Ne lève jamais d'exception (fail-safe,
    invariant #7, même patron que trend_strategy.evaluate_entry)."""
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
    if not candles_m15 or not candles_h1 or not candles_h4:
        return None

    votes = [
        compute_tf_vote(candles_m15, len(candles_m15) - 1),
        compute_tf_vote(candles_h1, len(candles_h1) - 1),
        compute_tf_vote(candles_h4, len(candles_h4) - 1),
    ]
    long_count = votes.count("long")
    short_count = votes.count("short")

    if long_count >= N_TF and long_count > short_count:
        direction = "long"
    elif short_count >= N_TF and short_count > long_count:
        direction = "short"
    else:
        return None

    entry_price = candles_m15[-1].close
    stop_price = _compute_stop(candles_m15, direction, entry_price)
    if stop_price is None:
        return None
    tp1, tp2 = compute_tp_levels(direction, entry_price, stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE)
    return TrendSignal(asset=asset, direction=direction, entry_price=entry_price, stop_price=stop_price, tp1=tp1, tp2=tp2)


def _compute_stop(candles: List[Candle], direction: str, entry_price: float) -> Optional[float]:
    """Stop = structure (dernier swing, canal de repli à
    STRUCTURE_LOOKBACK bougies) ou ATR(14) si la structure est
    indisponible — FIGÉ, aucun paramètre supplémentaire (pré-enregistrement)."""
    channel = compute_donchian_channel(candles, period=STRUCTURE_LOOKBACK)
    if channel is not None:
        highest, lowest = channel
        return lowest if direction == "long" else highest
    atr = compute_atr(candles, ATR_PERIOD)
    if atr is None:
        return None
    return entry_price - ATR_FALLBACK_MULT * atr if direction == "long" else entry_price + ATR_FALLBACK_MULT * atr
