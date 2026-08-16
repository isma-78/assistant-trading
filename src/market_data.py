"""
market_data.py — Données de marché en direct via Capital.com (§4.4 :
déterministe). Prix courant, bougies historiques, ATR(14), moyenne
mobile, taux de conversion EUR (remplace les constantes statiques
figées de `asset_whitelist.py` — TODO bloquant du palier P0, levé ici,
voir docs/DECISIONS.md).

Aucun LLM ici (invariant #1). Ce module lit et calcule, il ne décide
jamais : il fournit des chiffres à `validator.py`, `executor.py` et à la
construction dynamique de la liste blanche, rien de plus.
"""

from dataclasses import dataclass
from typing import List, Optional

from src.capital_client import CapitalApiError, CapitalClient


@dataclass(frozen=True)
class PriceSnapshot:
    epic: str
    bid: float
    ask: float
    mid: float
    market_status: str
    captured_at_broker: Optional[str]


@dataclass(frozen=True)
class Candle:
    time_utc: str
    open: float
    high: float
    low: float
    close: float


def get_price_snapshot(client: CapitalClient, epic: str) -> PriceSnapshot:
    snap = client.get_market_snapshot(epic)
    snapshot = snap.get("snapshot", {})
    bid, ask = snapshot.get("bid"), snapshot.get("offer")
    if bid is None or ask is None:
        raise CapitalApiError(f"Snapshot de marché incomplet pour {epic} : {snapshot}")
    return PriceSnapshot(
        epic=epic, bid=bid, ask=ask, mid=(bid + ask) / 2,
        market_status=snapshot.get("marketStatus", "UNKNOWN"),
        captured_at_broker=snapshot.get("updateTime"),
    )


def _mid_of(level: dict) -> Optional[float]:
    bid, ask = level.get("bid"), level.get("ask")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def get_candles(client: CapitalClient, epic: str, resolution: str = "HOUR", count: int = 50) -> List[Candle]:
    """Bougies en ordre chronologique (plus ancienne -> plus récente),
    prix médian (bid+ask)/2 à chaque point OHLC pour lisser le spread
    (choix documenté, docs/DECISIONS.md). Une bougie dont un des 4 prix
    médians est incalculable (bid ou ask manquant côté broker) est
    ignorée plutôt que de fausser silencieusement un calcul en aval."""
    raw = client.get_prices(epic, resolution=resolution, max_bars=count)
    candles = []
    for p in raw.get("prices", []):
        o = _mid_of(p.get("openPrice", {}))
        h = _mid_of(p.get("highPrice", {}))
        l = _mid_of(p.get("lowPrice", {}))
        c = _mid_of(p.get("closePrice", {}))
        if None in (o, h, l, c):
            continue
        candles.append(Candle(time_utc=p.get("snapshotTimeUTC") or p.get("snapshotTime", ""), open=o, high=h, low=l, close=c))
    return candles


def compute_atr(candles: List[Candle], period: int = 14) -> Optional[float]:
    """ATR de Wilder (définition standard du True Range moyenné par
    lissage exponentiel 1/period — pas une formule inventée pour ce
    projet, voir docs/DECISIONS.md pour la justification du choix).
    Retourne None si moins de period+1 bougies (pas assez d'historique
    pour un premier True Range)."""
    if len(candles) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        high, low = candles[i].high, candles[i].low
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_moving_average(candles: List[Candle], period: int = 200) -> Optional[float]:
    """Moyenne mobile simple des `period` dernières clôtures. Retourne
    None si pas assez d'historique."""
    if len(candles) < period:
        return None
    closes = [c.close for c in candles[-period:]]
    return sum(closes) / period


def get_eur_conversion_rate(client: CapitalClient, quote_currency: str) -> float:
    """Taux de conversion vers EUR pour une devise de cotation donnée
    ("EUR", "USD" ou "JPY"), calculé en direct via les prix spot
    EUR/USD et USD/JPY du broker — remplace les constantes
    `_USD_TO_EUR`/`_JPY_TO_EUR` figées au 16/08/2026 dans
    `asset_whitelist.py` (TODO bloquant avant exécution réelle, levé
    ici). Lève ValueError pour toute devise non gérée plutôt que de
    deviner un taux."""
    if quote_currency == "EUR":
        return 1.0
    if quote_currency == "USD":
        return 1.0 / get_price_snapshot(client, "EURUSD").mid
    if quote_currency == "JPY":
        eurusd = get_price_snapshot(client, "EURUSD").mid
        usdjpy = get_price_snapshot(client, "USDJPY").mid
        return 1.0 / (eurusd * usdjpy)
    raise ValueError(f"Devise de cotation non gérée pour la conversion EUR : {quote_currency!r}")
