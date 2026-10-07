"""
structural_characteristics.py — Partie 3 (08/10/2026),
docs/PROTOCOLE_COUPLES_08-10.md §1. Caractéristiques structurelles
calculées UNIQUEMENT sur des prix (bougies HOUR, bid/ask), jamais sur un
résultat de stratégie. Calcul pur, aucune I/O ici (l'orchestration —
lecture `data/historical/`, fenêtrage, fenêtre scellée — vit dans
`scripts/_couples_structurelles_08-10.py`).

Trois caractéristiques, définies exactement comme le protocole :
- coût/ATR : médiane(spread de clôture) / médiane(série ATR(14) Wilder) ;
- persistance : ratio de variance non chevauchant à l'échelle q=8 ;
- regroupement de volatilité : autocorrélation de Pearson de `|rendement|`
  au lag 1.
"""

import math
from statistics import median
from typing import List, Optional, Sequence

ATR_PERIOD = 14
VARIANCE_RATIO_SCALE = 8


def median_close_spread(bars: Sequence) -> Optional[float]:
    """Médiane de `close_ask - close_bid` sur les bougies fournies
    (`HistoricalBar`, voir `src.backtest_engine`). `None` si aucune
    bougie — jamais 0 par défaut."""
    spreads = [b.close_ask - b.close_bid for b in bars]
    return median(spreads) if spreads else None


def wilder_atr_series(bars: Sequence, period: int = ATR_PERIOD) -> List[float]:
    """Série ATR de Wilder (même formule que `src.market_data.compute_atr`,
    mais la série ENTIÈRE est retournée, pas seulement le dernier point)
    sur les prix MID. Liste vide si moins de `period + 1` bougies."""
    if len(bars) < period + 1:
        return []
    closes = [(b.close_bid + b.close_ask) / 2 for b in bars]
    highs = [(b.high_bid + b.high_ask) / 2 for b in bars]
    lows = [(b.low_bid + b.low_ask) / 2 for b in bars]
    true_ranges = []
    for i in range(1, len(bars)):
        prev_close = closes[i - 1]
        true_ranges.append(max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close)))

    series = []
    atr = sum(true_ranges[:period]) / period
    series.append(atr)
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
        series.append(atr)
    return series


def cost_over_atr(bars: Sequence, period: int = ATR_PERIOD) -> Optional[float]:
    """Médiane du spread de clôture / médiane de la série ATR(period).
    `None` si l'une des deux médianes est indisponible, ou si l'ATR
    médian vaut 0 (jamais une division par zéro silencieuse)."""
    spread = median_close_spread(bars)
    atr_series = wilder_atr_series(bars, period)
    if spread is None or not atr_series:
        return None
    atr_med = median(atr_series)
    if atr_med == 0:
        return None
    return spread / atr_med


def log_returns(bars: Sequence) -> List[float]:
    """Rendements logarithmiques 1-bougie sur les prix MID de clôture.
    Une bougie dont le prix précédent ou courant est <= 0 est ignorée
    (jamais un log indéfini) — fail-safe, inatteignable en pratique pour
    des prix de marché réels."""
    closes = [(b.close_bid + b.close_ask) / 2 for b in bars]
    returns = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev <= 0 or cur <= 0:
            continue
        returns.append(math.log(cur / prev))
    return returns


def variance_ratio(returns: Sequence[float], scale: int = VARIANCE_RATIO_SCALE) -> Optional[float]:
    """Ratio de variance NON CHEVAUCHANT à l'échelle `scale` (protocole
    §1b) : Var(rendements `scale`-bougies, blocs non chevauchants) /
    (`scale` × Var(rendements 1-bougie, même échantillon complet)).
    `None` si moins de 2 blocs complets, ou si la variance 1-bougie est
    nulle (jamais une division par zéro)."""
    n_blocks = len(returns) // scale
    if n_blocks < 2:
        return None
    block_sums = [sum(returns[i * scale:(i + 1) * scale]) for i in range(n_blocks)]
    var_1 = _sample_variance(returns)
    var_q = _sample_variance(block_sums)
    if var_1 is None or var_1 == 0 or var_q is None:
        return None
    return var_q / (scale * var_1)


def volatility_clustering(returns: Sequence[float]) -> Optional[float]:
    """Autocorrélation de Pearson de `|rendement|` au lag 1. `None` si
    moins de 3 rendements (corrélation non définie), ou si l'une des deux
    séries décalées a une variance nulle."""
    abs_returns = [abs(r) for r in returns]
    if len(abs_returns) < 3:
        return None
    x = abs_returns[:-1]
    y = abs_returns[1:]
    return _pearson(x, y)


def _sample_variance(values: Sequence[float]) -> Optional[float]:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / (n - 1)


def _pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n == 0:
        return None
    mean_x, mean_y = sum(x) / n, sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    if var_x == 0 or var_y == 0:
        return None
    return cov / math.sqrt(var_x * var_y)
