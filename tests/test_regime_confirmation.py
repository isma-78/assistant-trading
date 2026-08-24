from unittest.mock import MagicMock, patch

from src.capital_client import CapitalApiError
from src.regime_confirmation import (
    MA_PERIOD,
    compute_index_regimes,
    confirmation_indices,
    derive_confirmed_regime,
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


def _regime_response(direction):
    """Réponse get_prices produisant un régime MA200 donné ("long"/
    "short") pour n'importe quel epic interrogé."""
    resp = _prices_response(TREND_MA_PERIOD - 1, 100.0)
    level = 200.0 if direction == "long" else 50.0
    resp["prices"].append({
        "snapshotTimeUTC": "last",
        "openPrice": {"bid": level, "ask": level}, "highPrice": {"bid": level, "ask": level},
        "lowPrice": {"bid": level, "ask": level}, "closePrice": {"bid": level, "ask": level},
    })
    return resp


def test_ma_period_reexported_matches_trend_strategy():
    assert MA_PERIOD == TREND_MA_PERIOD


# --- confirmation_indices ---------------------------------------------------

def test_confirmation_indices_us30_confirmed_by_us100_only():
    assert confirmation_indices("US30") == ("US100",)


def test_confirmation_indices_us100_confirmed_by_us30_only():
    assert confirmation_indices("US100") == ("US30",)


def test_confirmation_indices_other_assets_confirmed_by_both():
    # BTCUSD/ETHUSD y compris depuis le retrait de l'exemption crypto
    # (23/08/2026, fin de journée) : même traitement que les 4 autres.
    for asset in ("GOLD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"):
        assert confirmation_indices(asset) == ("US30", "US100")


# --- compute_index_regimes ---------------------------------------------------

def test_compute_index_regimes_calls_both_indices_once_each():
    client = MagicMock()
    client.get_prices.side_effect = [_regime_response("long"), _regime_response("short")]
    regimes = compute_index_regimes(client, "HOUR")
    assert regimes == {"US30": "long", "US100": "short"}
    assert client.get_prices.call_count == 2
    first_call, second_call = client.get_prices.call_args_list
    assert first_call.args[0] == "US30"
    assert second_call.args[0] == "US100"
    assert first_call.kwargs == {"resolution": "HOUR", "max_bars": MA_PERIOD + 20}


def test_compute_index_regimes_per_index_fail_safe_none():
    # Une erreur sur UN indice donne None pour cet indice seul, jamais
    # une exception qui interromprait le calcul de l'autre.
    client = MagicMock()
    client.get_prices.side_effect = [RuntimeError("panne broker"), _regime_response("long")]
    regimes = compute_index_regimes(client, "HOUR")
    assert regimes == {"US30": None, "US100": "long"}


def test_compute_index_regimes_both_fail_safe_none():
    client = MagicMock()
    client.get_prices.side_effect = RuntimeError("panne broker")
    regimes = compute_index_regimes(client, "HOUR")
    assert regimes == {"US30": None, "US100": None}


# --- compute_index_regimes : retry avec backoff (24/08/2026, voir docs/DECISIONS.md) ---

def test_compute_index_regimes_retries_transient_429_then_succeeds():
    # Un 429 transitoire sur le premier appel de CHAQUE indice ne doit
    # plus donner None immédiatement — nouvel essai avant d'abandonner.
    client = MagicMock()
    client.get_prices.side_effect = [
        CapitalApiError("429 too many requests"), _regime_response("long"),  # US30 : échec puis succès
        _regime_response("short"),  # US100 : succès direct
    ]
    with patch("src.retry.time.sleep") as mock_sleep:
        regimes = compute_index_regimes(client, "HOUR")
    assert regimes == {"US30": "long", "US100": "short"}
    assert client.get_prices.call_count == 3
    mock_sleep.assert_called_once()


def test_compute_index_regimes_exhausts_retries_falls_back_to_none():
    # 429 persistant sur les 3 tentatives (défaut de retry_with_backoff) :
    # même comportement fail-safe qu'avant (None pour cet indice seul),
    # juste après avoir épuisé les essais supplémentaires.
    client = MagicMock()
    client.get_prices.side_effect = [
        CapitalApiError("429"), CapitalApiError("429"), CapitalApiError("429"),  # US30 : 3 échecs
        _regime_response("long"),  # US100 : succès direct
    ]
    with patch("src.retry.time.sleep") as mock_sleep:
        regimes = compute_index_regimes(client, "HOUR")
    assert regimes == {"US30": None, "US100": "long"}
    assert client.get_prices.call_count == 4
    assert mock_sleep.call_count == 2


def test_compute_index_regimes_non_network_error_not_retried():
    # RuntimeError n'est pas dans le tuple d'exceptions retenté par
    # retry_with_backoff — comportement fail-safe immédiat inchangé,
    # aucun essai supplémentaire, aucune pause.
    client = MagicMock()
    client.get_prices.side_effect = [RuntimeError("panne"), _regime_response("long")]
    with patch("src.retry.time.sleep") as mock_sleep:
        regimes = compute_index_regimes(client, "HOUR")
    assert regimes == {"US30": None, "US100": "long"}
    assert client.get_prices.call_count == 2
    mock_sleep.assert_not_called()


# --- derive_confirmed_regime --------------------------------------------------

def test_derive_confirmed_regime_us30_from_us100_alone():
    assert derive_confirmed_regime("US30", {"US100": "long", "US30": "short"}) == "long"


def test_derive_confirmed_regime_us100_from_us30_alone():
    assert derive_confirmed_regime("US100", {"US100": "long", "US30": "short"}) == "short"


def test_derive_confirmed_regime_other_asset_both_agree():
    assert derive_confirmed_regime("EURUSD", {"US30": "short", "US100": "short"}) == "short"


def test_derive_confirmed_regime_other_asset_disagree_none():
    assert derive_confirmed_regime("GOLD", {"US30": "long", "US100": "short"}) is None


def test_derive_confirmed_regime_missing_index_none():
    assert derive_confirmed_regime("EURUSD", {"US30": "long"}) is None
    assert derive_confirmed_regime("EURUSD", {}) is None


def test_derive_confirmed_regime_index_none_value_none():
    # compute_index_regimes peut écrire explicitement None (échec fail-safe).
    assert derive_confirmed_regime("EURUSD", {"US30": "long", "US100": None}) is None


def test_derive_confirmed_regime_indeterminate_index_regime_none():
    # compute_regime peut lui-même renvoyer None (historique insuffisant,
    # égalité stricte) — traité comme tout autre régime manquant.
    assert derive_confirmed_regime("US30", {"US100": None}) is None


def test_derive_confirmed_regime_crypto_same_rule_as_other_assets():
    # Retrait de l'exemption crypto (23/08/2026, fin de journée, voir
    # docs/DECISIONS.md) : BTCUSD/ETHUSD suivent désormais exactement la
    # même règle ET que les 4 autres actifs "génériques" — aucun
    # traitement spécial dans ce module.
    for asset in ("BTCUSD", "ETHUSD"):
        assert derive_confirmed_regime(asset, {"US30": "long", "US100": "long"}) == "long"
        assert derive_confirmed_regime(asset, {"US30": "long", "US100": "short"}) is None
