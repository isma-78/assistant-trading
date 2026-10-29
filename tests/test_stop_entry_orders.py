"""Ordres STOP d'entrée natifs : choix du type, stop de protection obligatoire,
client broker mocké (aucun appel réel)."""
from unittest.mock import MagicMock

import pytest

from src.capital_client import CapitalApiError, CapitalClient
from src.entry_filters import (
    ORDER_LIMIT, ORDER_STOP, choose_entry_order_type, stop_protection_rejection,
)


@pytest.mark.parametrize("direction,level,expected", [
    ("long", 99.0, ORDER_LIMIT),     # achat sous l'ask : limite
    ("long", 101.0, ORDER_STOP),     # buy-stop au-dessus
    ("long", 100.0, None),           # exactement sur l'ask : refus
    ("short", 101.0, ORDER_LIMIT),   # vente au-dessus du bid : limite
    ("short", 98.0, ORDER_STOP),     # sell-stop en dessous
    ("short", 99.5, None),           # exactement sur le bid : refus
    ("flat", 100.0, None),
])
def test_choose_entry_order_type(direction, level, expected):
    assert choose_entry_order_type(direction, level, 99.5, 100.0) == expected


def test_choose_entry_order_type_without_prices_refuses():
    assert choose_entry_order_type("long", 101.0, None, 100.0) is None
    assert choose_entry_order_type("long", 101.0, 99.0, None) is None


@pytest.mark.parametrize("distance", [None, 0, -1.0, float("inf"), float("nan"), "x"])
def test_stop_protection_rejects_missing_or_invalid_distance(distance):
    assert stop_protection_rejection("long", 100.0, distance) is not None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_stop_protection_accepts_positive_distance(direction):
    assert stop_protection_rejection(direction, 100.0, 1.0) is None


def _client():
    client = CapitalClient.__new__(CapitalClient)
    client.post = MagicMock(return_value={"dealReference": "ref"})
    client.get = MagicMock(return_value={
        "dealStatus": "ACCEPTED", "level": 101.0, "affectedDeals": [{"dealId": "d1"}],
    })
    return client


def test_client_sends_stop_type_with_guaranteed_stop():
    client = _client()
    out = client.place_stop_entry_order("EURUSD", "BUY", 1000, 1.1, True, 0.002)
    path, body = client.post.call_args.args
    assert path == "/workingorders"
    assert body["type"] == "STOP" and body["guaranteedStop"] is True and body["stopDistance"] == 0.002
    assert body["level"] == 1.1 and body["direction"] == "BUY"
    assert out["deal_id"] == "d1"


@pytest.mark.parametrize("guaranteed,distance", [(False, 0.002), (True, None), (True, 0), (True, -1)])
def test_client_refuses_stop_order_without_protective_stop(guaranteed, distance):
    client = _client()
    with pytest.raises(ValueError):
        client.place_stop_entry_order("EURUSD", "BUY", 1000, 1.1, guaranteed, distance)
    client.post.assert_not_called()


def test_client_refuses_unknown_direction():
    client = _client()
    with pytest.raises(ValueError):
        client.place_stop_entry_order("EURUSD", "long", 1000, 1.1, True, 0.002)
    client.post.assert_not_called()


def test_client_raises_when_broker_rejects_stop_order():
    client = _client()
    client.get = MagicMock(return_value={"dealStatus": "REJECTED", "reason": "X"})
    with pytest.raises(CapitalApiError):
        client.place_stop_entry_order("EURUSD", "SELL", 1000, 1.1, True, 0.002)


def test_stale_stop_order_is_cancelled_like_any_working_order(tmp_path):
    from datetime import datetime, timedelta, timezone
    from src.db import init_db
    from src.executor import cancel_stale_working_orders
    db = str(tmp_path / "t.db")
    init_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
    client = MagicMock()
    client.get_working_orders.return_value = [
        {"workingOrderData": {"dealId": "stop-1", "createdDateUTC": old, "orderType": "STOP"}}
    ]
    assert cancel_stale_working_orders(db, client, max_age_seconds=60) == 1
    client.cancel_working_order.assert_called_once_with("stop-1")
