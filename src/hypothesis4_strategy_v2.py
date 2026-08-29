"""
hypothesis4_strategy_v2.py — Hypothèse #4, refonte L4 (29/08/2026,
pré-enregistré dans docs/HYPOTHESES.md AVANT tout calcul). Divergence
prix/RSI(14) ET prix/OBV entre deux pivots fractals CAUSAUX. Remplace
`mean_reversion_strategy.py` (archivé, voir archive/ — la sortie
standard §2.10 du projet, TP1/TP2/trailing, reste inchangée, ce module
ne fournit que le déclencheur d'entrée).

Risque explicitement identifié par Ismaël avant construction (degrés de
liberté + lookahead) : la définition du pivot est FIGÉE ici
(`find_confirmed_pivots`, fractal à `PIVOT_FRACTAL_N` bougies), jamais
improvisée après coup. Anti-lookahead garanti PAR CONSTRUCTION : la
borne de boucle `range(fractal_n, len(candles) - fractal_n)` exclut
mécaniquement tout index dont la fenêtre de confirmation dépasserait la
longueur de la liste fournie — un pivot ne peut structurellement pas
être rapporté avant que `fractal_n` bougies existent après lui. Voir
tests/test_hypothesis4_strategy_v2.py, écrits AVANT ce fichier.

OBV : `Candle.volume` est le `lastTradedVolume` brut Capital.com, déjà
identifié comme volume TICK (nombre de transactions), pas un volume
réel échangé (docs/DECISIONS.md, 25/08/2026) — utilisé ici comme
indicateur de momentum de PARTICIPATION relatif, jamais de liquidité
absolue. `require_obv_confirmation` (jamais une variable de grille,
un paramètre diagnostique) permet de rejouer la même configuration sans
la jambe OBV, comme exigé (point 7 du pré-enregistrement) — la
comparaison est POST-HOC sur le variant déjà sélectionné, elle n'entre
jamais dans la recherche de grille.
"""

from typing import List, Optional, Tuple

from src.market_data import Candle, compute_atr
from src.trend_strategy import TrendSignal, compute_tp_levels

RSI_PERIOD = 14  # FIGÉ — convention déjà établie ailleurs dans le projet (H5 V1/V2/V3)
ATR_PERIOD = 14  # FIGÉ
# Structure de sortie STANDARD du projet (§2.10 : TP1 50% à 1R / TP2 30%
# à 2R / reliquat 20% trailing 2×ATR) — remplace le TP fixe unique/stop
# fixe/aucun trailing de l'ancien mean_reversion_strategy.py (archivé),
# décision explicite du pré-enregistrement du 29/08/2026 ("H1-H4 gardent
# la structure standard du projet"). Mêmes valeurs que H2/H3/H5, pas un
# second choix indépendant.
TP1_R_MULTIPLE = 1.0
TP2_R_MULTIPLE = 2.0

# Variables AJUSTÉES (pré-enregistrement 29/08/2026, docs/HYPOTHESES.md) —
# valeurs par défaut = point médian de la grille déclarée, remplacées par
# hypothesis_params.apply_overrides une fois la calibration validée.
PIVOT_FRACTAL_N = 3
MAX_PIVOT_DISTANCE_BARS = 40
STOP_ATR_MULT = 1.5

OVERRIDABLE = ["PIVOT_FRACTAL_N", "MAX_PIVOT_DISTANCE_BARS", "STOP_ATR_MULT"]


def find_confirmed_pivots(candles: List[Candle], fractal_n: int) -> List[Tuple[int, str, float]]:
    """Pivots fractals CONFIRMÉS uniquement : un haut/bas à l'index i
    n'est retenu que si `fractal_n` bougies existent de PART ET D'AUTRE
    dans `candles` — la borne supérieure de la boucle
    (`len(candles) - fractal_n`) rend structurellement impossible de
    rapporter un pivot dont la fenêtre de confirmation déborderait la
    liste fournie (anti-lookahead par construction, pas par
    vérification a posteriori)."""
    pivots: List[Tuple[int, str, float]] = []
    n = fractal_n
    if len(candles) < 2 * n + 1:
        return pivots
    for i in range(n, len(candles) - n):
        before = candles[i - n:i]
        after = candles[i + 1:i + n + 1]
        if candles[i].high > max(c.high for c in before) and candles[i].high > max(c.high for c in after):
            pivots.append((i, "high", candles[i].high))
        if candles[i].low < min(c.low for c in before) and candles[i].low < min(c.low for c in after):
            pivots.append((i, "low", candles[i].low))
    return pivots


def compute_rsi_series(candles: List[Candle], period: int = RSI_PERIOD) -> List[Optional[float]]:
    """RSI de Wilder à CHAQUE index (`None` tant que l'historique est
    insuffisant à cet index précis) — chaque valeur ne dépend que des
    bougies jusqu'à cet index inclus, jamais au-delà (lissage
    incrémental standard, même formule que market_data.compute_atr)."""
    n = len(candles)
    series: List[Optional[float]] = [None] * n
    if n < period + 1:
        return series
    gains = [max(candles[i].close - candles[i - 1].close, 0.0) for i in range(1, n)]
    losses = [max(candles[i - 1].close - candles[i].close, 0.0) for i in range(1, n)]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    series[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    for idx in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
        rsi = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
        series[idx + 1] = rsi
    return series


def compute_obv(candles: List[Candle]) -> Optional[List[float]]:
    """On-Balance Volume cumulatif. `None` si UN SEUL volume manque sur
    toute la série fournie (fail-safe : absence de donnée n'est jamais
    devinée, même convention que `causal_decomposition.
    is_cout_sortie_plausible`)."""
    if any(c.volume is None for c in candles):
        return None
    obv = [0.0]
    for i in range(1, len(candles)):
        if candles[i].close > candles[i - 1].close:
            obv.append(obv[-1] + candles[i].volume)
        elif candles[i].close < candles[i - 1].close:
            obv.append(obv[-1] - candles[i].volume)
        else:
            obv.append(obv[-1])
    return obv


def evaluate_entry(
    asset: str, candles: Optional[List[Candle]], require_obv_confirmation: bool = True,
) -> Optional[TrendSignal]:
    """Point d'entrée unique : divergence prix/RSI (ET prix/OBV si
    `require_obv_confirmation`) entre deux pivots fractals du même type,
    confirmée EXACTEMENT à la bougie qui vient de confirmer le second
    pivot (événement ponctuel, pas un état persistant — ne refire jamais
    aux cycles suivants pour la même paire de pivots).

    Ne lève jamais d'exception (fail-safe, invariant #7, même patron que
    trend_strategy.evaluate_entry)."""
    try:
        return _evaluate_entry(asset, candles, require_obv_confirmation)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: Optional[List[Candle]], require_obv_confirmation: bool) -> Optional[TrendSignal]:
    if not candles:
        return None
    n = PIVOT_FRACTAL_N
    last_index = len(candles) - 1
    if last_index < 2 * n:
        return None

    pivots = find_confirmed_pivots(candles, n)
    rsi_series = compute_rsi_series(candles, RSI_PERIOD)
    obv_series = compute_obv(candles) if require_obv_confirmation else None
    atr = compute_atr(candles, ATR_PERIOD)
    if atr is None:
        return None

    for pivot_type, direction in (("low", "long"), ("high", "short")):
        same_type = [p for p in pivots if p[1] == pivot_type]
        # Le second pivot doit se confirmer EXACTEMENT à la bougie courante.
        second_candidates = [p for p in same_type if p[0] + n == last_index]
        if not second_candidates:
            continue
        i2 = second_candidates[0][0]

        earlier = [p for p in same_type if p[0] < i2 and i2 - p[0] <= MAX_PIVOT_DISTANCE_BARS]
        if not earlier:
            continue
        i1 = max(earlier, key=lambda p: p[0])[0]

        rsi_i1, rsi_i2 = rsi_series[i1], rsi_series[i2]
        if rsi_i1 is None or rsi_i2 is None:
            continue

        obv_ok = True
        if require_obv_confirmation:
            if obv_series is None:
                continue
            obv_ok = (obv_series[i2] > obv_series[i1]) if direction == "long" else (obv_series[i2] < obv_series[i1])

        if direction == "long":
            price_confirms = candles[i2].low < candles[i1].low
            rsi_confirms = rsi_i2 > rsi_i1
            if price_confirms and rsi_confirms and obv_ok:
                return _build_signal(asset, "long", candles[-1].close, candles[i2].low - STOP_ATR_MULT * atr)
        else:
            price_confirms = candles[i2].high > candles[i1].high
            rsi_confirms = rsi_i2 < rsi_i1
            if price_confirms and rsi_confirms and obv_ok:
                return _build_signal(asset, "short", candles[-1].close, candles[i2].high + STOP_ATR_MULT * atr)
    return None


def _build_signal(asset: str, direction: str, entry_price: float, stop_price: float) -> TrendSignal:
    tp1, tp2 = compute_tp_levels(direction, entry_price, stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE)
    return TrendSignal(asset=asset, direction=direction, entry_price=entry_price, stop_price=stop_price, tp1=tp1, tp2=tp2)
