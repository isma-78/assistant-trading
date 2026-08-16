"""
Tests de market_data — prix, bougies, ATR, moyenne mobile, conversion EUR.
CapitalClient est simulé (MagicMock), aucun appel réseau réel.
"""

from unittest.mock import MagicMock

import pytest

from src.capital_client import CapitalApiError
from src.market_data import (
    Candle,
    compute_atr,
    compute_moving_average,
    get_candles,
    get_eur_conversion_rate,
    get_price_snapshot,
)


def _client_with_snapshot(bid, ask, market_status="TRADEABLE"):
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": bid, "offer": ask, "marketStatus": market_status, "updateTime": "2026-08-16T12:00:00"}
    }
    return client


def test_get_price_snapshot_computes_mid():
    client = _client_with_snapshot(1.1560, 1.1562)
    snap = get_price_snapshot(client, "EURUSD")
    assert snap.bid == 1.1560
    assert snap.ask == 1.1562
    assert snap.mid == pytest.approx(1.1561)
    assert snap.market_status == "TRADEABLE"


def test_get_price_snapshot_missing_bid_ask_raises():
    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"marketStatus": "CLOSED"}}
    with pytest.raises(CapitalApiError):
        get_price_snapshot(client, "EURUSD")


def test_get_candles_computes_mid_ohlc_in_order():
    client = MagicMock()
    client.get_prices.return_value = {
        "prices": [
            {
                "snapshotTimeUTC": "2026-08-16T10:00:00",
                "openPrice": {"bid": 100.0, "ask": 100.2},
                "highPrice": {"bid": 101.0, "ask": 101.2},
                "lowPrice": {"bid": 99.0, "ask": 99.2},
                "closePrice": {"bid": 100.5, "ask": 100.7},
            },
            {
                "snapshotTimeUTC": "2026-08-16T11:00:00",
                "openPrice": {"bid": 100.5, "ask": 100.7},
                "highPrice": {"bid": 102.0, "ask": 102.2},
                "lowPrice": {"bid": 100.0, "ask": 100.2},
                "closePrice": {"bid": 101.5, "ask": 101.7},
            },
        ]
    }
    candles = get_candles(client, "EURUSD", resolution="HOUR", count=2)
    assert len(candles) == 2
    assert candles[0].time_utc == "2026-08-16T10:00:00"
    assert candles[0].open == pytest.approx(100.1)
    assert candles[0].close == pytest.approx(100.6)
    assert candles[1].close == pytest.approx(101.6)


def test_get_candles_skips_incomplete_bars():
    client = MagicMock()
    client.get_prices.return_value = {
        "prices": [
            {
                "snapshotTimeUTC": "t1",
                "openPrice": {"bid": 100.0},  # ask manquant -> ignorée
                "highPrice": {"bid": 101.0, "ask": 101.2},
                "lowPrice": {"bid": 99.0, "ask": 99.2},
                "closePrice": {"bid": 100.5, "ask": 100.7},
            },
            {
                "snapshotTimeUTC": "t2",
                "openPrice": {"bid": 100.5, "ask": 100.7},
                "highPrice": {"bid": 102.0, "ask": 102.2},
                "lowPrice": {"bid": 100.0, "ask": 100.2},
                "closePrice": {"bid": 101.5, "ask": 101.7},
            },
        ]
    }
    candles = get_candles(client, "EURUSD")
    assert len(candles) == 1
    assert candles[0].time_utc == "t2"


def _flat_candles(closes):
    """Bougies synthétiques sans mèche (high=low=close=open) pour des
    calculs d'ATR/MA prévisibles à la main."""
    return [Candle(time_utc=str(i), open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


def test_compute_atr_none_when_not_enough_candles():
    candles = _flat_candles([100.0] * 10)  # besoin de period+1 = 15
    assert compute_atr(candles, period=14) is None


def test_compute_atr_zero_when_no_price_movement():
    # High=low=close=open partout et closes identiques : True Range = 0
    # à chaque bougie -> ATR = 0, pas None (assez d'historique).
    candles = _flat_candles([100.0] * 16)
    assert compute_atr(candles, period=14) == 0.0


def test_compute_atr_known_value_hand_computed():
    # 3 bougies, period=2 : True Range hors mèche = |close_i - close_{i-1}|
    # (high=low=close ici). TR1=|101-100|=1, TR2=|103-101|=2.
    # Seed = moyenne des 2 premiers TR = (1+2)/2 = 1.5. Pas de lissage
    # supplémentaire (aucun TR au-delà de period=2 avec seulement 2 TR).
    candles = _flat_candles([100.0, 101.0, 103.0])
    atr = compute_atr(candles, period=2)
    assert atr == pytest.approx(1.5)


def test_compute_moving_average_none_when_not_enough_candles():
    candles = _flat_candles([100.0] * 5)
    assert compute_moving_average(candles, period=10) is None


def test_compute_moving_average_uses_last_n_closes():
    closes = [100.0, 200.0, 10.0, 20.0, 30.0]  # MA(3) sur les 3 dernières
    candles = _flat_candles(closes)
    ma = compute_moving_average(candles, period=3)
    assert ma == pytest.approx((10.0 + 20.0 + 30.0) / 3)


def test_get_eur_conversion_rate_eur_is_identity():
    client = MagicMock()
    assert get_eur_conversion_rate(client, "EUR") == 1.0
    client.get_market_snapshot.assert_not_called()


def test_get_eur_conversion_rate_usd_uses_eurusd_inverse():
    client = _client_with_snapshot(1.1560, 1.1562)  # mid = 1.1561
    rate = get_eur_conversion_rate(client, "USD")
    assert rate == pytest.approx(1 / 1.1561)


def test_get_eur_conversion_rate_jpy_uses_eurusd_and_usdjpy():
    client = MagicMock()

    def snapshot_for(epic):
        if epic == "EURUSD":
            return {"snapshot": {"bid": 1.1560, "offer": 1.1562, "marketStatus": "TRADEABLE"}}
        if epic == "USDJPY":
            return {"snapshot": {"bid": 159.20, "offer": 159.40, "marketStatus": "TRADEABLE"}}
        raise AssertionError(f"epic inattendu : {epic}")

    client.get_market_snapshot.side_effect = snapshot_for

    rate = get_eur_conversion_rate(client, "JPY")

    eurusd_mid = (1.1560 + 1.1562) / 2
    usdjpy_mid = (159.20 + 159.40) / 2
    assert rate == pytest.approx(1 / (eurusd_mid * usdjpy_mid))


def test_get_eur_conversion_rate_unknown_currency_raises():
    client = MagicMock()
    with pytest.raises(ValueError):
        get_eur_conversion_rate(client, "GBP")
