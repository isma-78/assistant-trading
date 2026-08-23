from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.regime_confirmation import (
    CRYPTO_ASSETS,
    MA_PERIOD,
    _confirm_regime,
    confirm_regime,
    confirmation_indices,
)
from src.trend_strategy import MA_PERIOD as TREND_MA_PERIOD


def _prices_response(n, level):
    """N bougies plates au niveau `level` -> compute_regime indéterminé
    (égalité) sauf sur la dernière, ajustée par l'appelant si besoin."""
    return {
        "prices": [
            {
                "snapshotTimeUTC": str(i),
                "openPrice": {"bid": level, "ask": level}, "highPrice": {"bid": level, "ask": level},
                "lowPrice": {"bid": level, "ask": level}, "closePrice": {"bid": level, "ask": level},
            }
            for i in range(n)
        ]
    }


def _client_with_regime(direction):
    """Client factice dont get_prices produit un régime MA200 donné
    ("long"/"short") sur n'importe quel epic interrogé, quelle que soit
    la résolution passée."""
    client = MagicMock()
    resp = _prices_response(TREND_MA_PERIOD - 1, 100.0)
    resp["prices"].append({
        "snapshotTimeUTC": "last",
        "openPrice": {"bid": 200.0 if direction == "long" else 50.0, "ask": 200.0 if direction == "long" else 50.0},
        "highPrice": {"bid": 200.0 if direction == "long" else 50.0, "ask": 200.0 if direction == "long" else 50.0},
        "lowPrice": {"bid": 200.0 if direction == "long" else 50.0, "ask": 200.0 if direction == "long" else 50.0},
        "closePrice": {"bid": 200.0 if direction == "long" else 50.0, "ask": 200.0 if direction == "long" else 50.0},
    })
    client.get_prices.return_value = resp
    return client


def test_ma_period_reexported_matches_trend_strategy():
    assert MA_PERIOD == TREND_MA_PERIOD


# --- confirmation_indices ---------------------------------------------------

def test_confirmation_indices_us30_confirmed_by_us100_only():
    assert confirmation_indices("US30") == ("US100",)


def test_confirmation_indices_us100_confirmed_by_us30_only():
    assert confirmation_indices("US100") == ("US30",)


def test_confirmation_indices_other_assets_confirmed_by_both():
    for asset in ("GOLD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"):
        assert confirmation_indices(asset) == ("US30", "US100")


# --- confirm_regime — session Asie : pass-through ---------------------------

def test_confirm_regime_asia_session_always_true_no_client_call():
    client = MagicMock()
    now = datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc)
    assert confirm_regime(client, "EURUSD", "long", "HOUR", now) is True
    client.get_prices.assert_not_called()


# --- confirm_regime — Londres/New York : ET strict --------------------------

def test_confirm_regime_london_single_index_asset_matches():
    # US30 confirmé par US100 seul.
    client = _client_with_regime("long")
    now = datetime(2026, 8, 24, 8, 15, tzinfo=timezone.utc)
    assert confirm_regime(client, "US30", "long", "HOUR", now) is True
    client.get_prices.assert_called_once_with("US100", resolution="HOUR", max_bars=MA_PERIOD + 20)


def test_confirm_regime_ny_single_index_asset_mismatch():
    client = _client_with_regime("short")
    now = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    assert confirm_regime(client, "US100", "long", "HOUR", now) is False


def test_confirm_regime_other_asset_both_indices_match():
    client = _client_with_regime("short")
    now = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    assert confirm_regime(client, "EURUSD", "short", "HOUR", now) is True
    assert client.get_prices.call_count == 2


def test_confirm_regime_other_asset_first_index_mismatch_short_circuits():
    client = _client_with_regime("short")
    now = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    assert confirm_regime(client, "GOLD", "long", "HOUR", now) is False
    # ET court-circuite au premier désaccord (US30 en premier) : un seul appel.
    client.get_prices.assert_called_once()


def test_confirm_regime_other_asset_second_index_mismatch():
    # EURUSD (pas BTCUSD : la crypto est exemptée, voir plus bas).
    client = MagicMock()
    long_resp = _client_with_regime("long").get_prices.return_value
    short_resp = _client_with_regime("short").get_prices.return_value
    client.get_prices.side_effect = [long_resp, short_resp]  # US30 concorde, US100 non
    now = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    assert confirm_regime(client, "EURUSD", "long", "HOUR", now) is False
    assert client.get_prices.call_count == 2


# --- fail-safe ---------------------------------------------------------------

def test_confirm_regime_naive_datetime_is_fail_safe_false():
    client = MagicMock()
    naive_now = datetime(2026, 8, 24, 8, 0)  # pas de tzinfo
    assert confirm_regime(client, "EURUSD", "long", "HOUR", naive_now) is False


def test_underscore_confirm_regime_raises_on_naive_datetime():
    client = MagicMock()
    naive_now = datetime(2026, 8, 24, 8, 0)
    with pytest.raises(ValueError):
        _confirm_regime(client, "EURUSD", "long", "HOUR", naive_now)


def test_confirm_regime_unreachable_hour_is_fail_safe_false():
    # Défensif : n'arrive jamais via le gate de session en production,
    # mais _confirm_regime elle-même doit échouer fermé si jamais atteinte.
    client = MagicMock()
    now = datetime(2026, 8, 24, 5, 0, tzinfo=timezone.utc)
    assert confirm_regime(client, "EURUSD", "long", "HOUR", now) is False
    client.get_prices.assert_not_called()


def test_confirm_regime_internal_exception_is_fail_safe_false():
    client = MagicMock()
    client.get_prices.side_effect = RuntimeError("panne broker")
    now = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    assert confirm_regime(client, "EURUSD", "long", "HOUR", now) is False


# --- crypto (BTCUSD/ETHUSD) — exemption, analyse continue (23/08/2026) ------

def test_crypto_assets_are_btcusd_ethusd():
    assert CRYPTO_ASSETS == ("BTCUSD", "ETHUSD")


def test_confirm_regime_crypto_always_true_no_client_call():
    client = MagicMock()
    for asset in CRYPTO_ASSETS:
        # Heure de session ET heure hors-session : toujours True, jamais
        # d'appel réseau — la crypto n'a pas d'indice à interroger.
        for hour in (0, 8, 13, 5, 17, 23):
            now = datetime(2026, 8, 24, hour, 0, tzinfo=timezone.utc)
            assert confirm_regime(client, asset, "long", "HOUR", now) is True
    client.get_prices.assert_not_called()


def test_confirm_regime_crypto_true_even_on_naive_datetime_hour_branch():
    # La vérification de l'actif crypto passe AVANT l'accès à `now.hour`
    # mais APRÈS la garde `tzinfo`, qui reste stricte pour tous les
    # actifs (invariant #7 — jamais assoupli pour la crypto).
    client = MagicMock()
    naive_now = datetime(2026, 8, 24, 8, 0)
    assert confirm_regime(client, "BTCUSD", "long", "HOUR", naive_now) is False
