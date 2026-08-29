"""
hypothesis1_strategy_v2.py — Hypothèse #1, refonte L1 (29/08/2026,
pré-enregistré dans docs/HYPOTHESES.md AVANT tout calcul). Régime ADX :
direction = signe de la pente d'une MA longue, entrée = ADX(14) franchit
un seuil à la hausse avec la pente confirmant la direction, stop =
ATR(14) × k. Remplace `trend_strategy.evaluate_entry`/`compute_regime`
(MA200 + rupture Donchian(20)) — `trend_strategy.py` N'EST PAS archivé :
`TrendSignal`/`compute_tp_levels`/`compute_donchian_channel`/
`compute_trailing_stop_channel` restent des utilitaires partagés par
toutes les hypothèses (voir docs/DECISIONS.md).
"""

from typing import List, Optional

from src.market_data import Candle, compute_atr, compute_moving_average
from src.trend_strategy import TrendSignal, compute_tp_levels

ADX_PERIOD = 14  # FIGÉ — période standard universelle
ATR_PERIOD = 14  # FIGÉ
SLOPE_LOOKBACK = 5  # FIGÉ — mesure de pente courte, non balayée indépendamment de ma_period
TP1_R_MULTIPLE = 1.0  # FIGÉ, structure de sortie standard §2.10
TP2_R_MULTIPLE = 2.0  # FIGÉ

# Variables AJUSTÉES (pré-enregistrement 29/08/2026, docs/HYPOTHESES.md) —
# valeurs par défaut = point médian de la grille, remplacées par
# hypothesis_params.apply_overrides une fois la calibration validée.
MA_PERIOD = 200
ADX_THRESHOLD = 25.0
K_ATR = 2.0

OVERRIDABLE = ["MA_PERIOD", "ADX_THRESHOLD", "K_ATR"]


def compute_adx_series(candles: List[Candle], period: int = ADX_PERIOD) -> List[Optional[float]]:
    """ADX de Wilder (True Range + Directional Movement lissés,
    définition standard — pas une formule inventée pour ce projet).
    Retourne une valeur PAR INDEX (`None` tant que l'historique est
    insuffisant à cet index précis), chaque valeur ne dépendant que des
    bougies jusqu'à cet index inclus."""
    n = len(candles)
    result: List[Optional[float]] = [None] * n
    if n < 2 * period + 1:
        return result

    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        high, low = candles[i].high, candles[i].low
        prev_high, prev_low, prev_close = candles[i - 1].high, candles[i - 1].low, candles[i - 1].close
        tr[i] = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    def _dx(s_tr: float, s_plus: float, s_minus: float) -> float:
        if s_tr == 0:
            return 0.0
        plus_di = 100.0 * s_plus / s_tr
        minus_di = 100.0 * s_minus / s_tr
        denom = plus_di + minus_di
        return 100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0

    smoothed_tr = sum(tr[1:period + 1])
    smoothed_plus = sum(plus_dm[1:period + 1])
    smoothed_minus = sum(minus_dm[1:period + 1])

    dx_by_index = {period: _dx(smoothed_tr, smoothed_plus, smoothed_minus)}
    for i in range(period + 1, n):
        smoothed_tr = smoothed_tr - smoothed_tr / period + tr[i]
        smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm[i]
        smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm[i]
        dx_by_index[i] = _dx(smoothed_tr, smoothed_plus, smoothed_minus)

    dx_indices = sorted(dx_by_index)
    # `n >= 2*period+1` (garde en tête de fonction) garantit toujours
    # `len(dx_indices) >= period` — aucun cas où cette condition serait
    # fausse, pas de vérification redondante sur une branche inatteignable.
    first_adx_idx = dx_indices[period - 1]
    adx = sum(dx_by_index[idx] for idx in dx_indices[:period]) / period
    result[first_adx_idx] = adx
    for idx in dx_indices[period:]:
        adx = (adx * (period - 1) + dx_by_index[idx]) / period
        result[idx] = adx
    return result


def _ma_slope_direction(candles: List[Candle], period: int, lookback: int) -> Optional[str]:
    """Signe de la pente de MA(`period`) sur les `lookback` dernières
    bougies. None si historique insuffisant ou pente nulle (cas
    dégénéré, aucun régime tranché)."""
    if len(candles) < period + lookback:
        return None
    ma_now = compute_moving_average(candles, period)
    ma_before = compute_moving_average(candles[:-lookback], period)
    if ma_now is None or ma_before is None or ma_now == ma_before:
        return None
    return "long" if ma_now > ma_before else "short"


def evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    """Point d'entrée unique : ADX(14) franchit `ADX_THRESHOLD` à la
    hausse (entre l'avant-dernière et la dernière bougie), pente de
    MA(`MA_PERIOD`) confirmant la même direction. Ne lève jamais
    d'exception (fail-safe, invariant #7, même patron que
    trend_strategy.evaluate_entry)."""
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

    adx_series = compute_adx_series(candles, ADX_PERIOD)
    adx_now, adx_prev = adx_series[-1], adx_series[-2]
    if adx_now is None or adx_prev is None:
        return None
    if not (adx_prev <= ADX_THRESHOLD < adx_now):
        return None  # pas un FRANCHISSEMENT à la hausse, juste un état déjà établi ou une baisse

    # ATR(14) exige moins d'historique que le franchissement ADX déjà
    # validé ci-dessus (15 bougies contre 2×ADX_PERIOD+1=29) : toujours
    # disponible à ce stade, jamais None — pas de vérification
    # redondante sur une branche inatteignable.
    atr = compute_atr(candles, ATR_PERIOD)

    entry_price = candles[-1].close
    stop_price = entry_price - K_ATR * atr if direction == "long" else entry_price + K_ATR * atr
    tp1, tp2 = compute_tp_levels(direction, entry_price, stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE)
    return TrendSignal(asset=asset, direction=direction, entry_price=entry_price, stop_price=stop_price, tp1=tp1, tp2=tp2)
