"""
Tests de capital_client — client HTTP Capital.com. Aucun appel réseau
réel : la session `requests` est simulée via injection de dépendance
(paramètre `session=` du constructeur).
"""

from unittest.mock import MagicMock

import pytest
import requests

from src.capital_client import CapitalApiError, CapitalClient


def _fake_response(json_body=None, headers=None, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_body or {}
    resp.headers = headers or {}
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = requests.HTTPError("boom")
    return resp


def _client_with_session():
    session = MagicMock()
    client = CapitalClient("key", "id", "pw", "https://api.example.com/api/v1", session=session)
    return client, session


def test_login_stores_tokens_from_headers():
    client, session = _client_with_session()
    session.post.return_value = _fake_response(headers={"CST": "cst-val", "X-SECURITY-TOKEN": "sec-val"})

    tokens = client.login()

    assert tokens == {"CST": "cst-val", "X-SECURITY-TOKEN": "sec-val"}
    session.post.assert_called_once()
    call_kwargs = session.post.call_args
    assert call_kwargs.kwargs["headers"]["X-CAP-API-KEY"] == "key"


def test_login_missing_tokens_raises_capital_api_error():
    client, session = _client_with_session()
    session.post.return_value = _fake_response(headers={})

    with pytest.raises(CapitalApiError):
        client.login()


def test_get_before_login_raises():
    client, _ = _client_with_session()
    with pytest.raises(CapitalApiError):
        client.get("/accounts")


def test_get_after_login_includes_tokens_in_headers():
    client, session = _client_with_session()
    session.post.return_value = _fake_response(headers={"CST": "c", "X-SECURITY-TOKEN": "s"})
    client.login()
    session.get.return_value = _fake_response(json_body={"ok": True})

    result = client.get("/accounts")

    assert result == {"ok": True}
    headers_used = session.get.call_args.kwargs["headers"]
    assert headers_used["CST"] == "c"
    assert headers_used["X-SECURITY-TOKEN"] == "s"
    assert headers_used["X-CAP-API-KEY"] == "key"


def test_http_error_wrapped_as_capital_api_error():
    client, session = _client_with_session()
    session.post.return_value = _fake_response(headers={"CST": "c", "X-SECURITY-TOKEN": "s"})
    client.login()
    session.get.return_value = _fake_response(status_ok=False)

    with pytest.raises(CapitalApiError):
        client.get("/accounts")


def test_http_error_includes_response_body_for_diagnosis():
    # Bug réel trouvé le 16/08/2026 : str(HTTPError) seul n'inclut jamais
    # errorCode, le seul indice exploitable pour diagnostiquer un rejet
    # Capital.com (ex: "error.vallidation.guaranteed-stop-loss.required").
    client, session = _client_with_session()
    session.post.return_value = _fake_response(headers={"CST": "c", "X-SECURITY-TOKEN": "s"})
    client.login()
    error_response = _fake_response(status_ok=False)
    error_response.text = '{"errorCode":"error.vallidation.guaranteed-stop-loss.required"}'
    session.get.return_value = error_response

    with pytest.raises(CapitalApiError, match="guaranteed-stop-loss"):
        client.get("/accounts")


def _logged_in_client():
    client, session = _client_with_session()
    session.post.return_value = _fake_response(headers={"CST": "c", "X-SECURITY-TOKEN": "s"})
    client.login()
    session.post.reset_mock()
    return client, session


def test_get_account_balance_returns_preferred_account():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={
        "accounts": [
            {"preferred": False, "currency": "USD", "balance": {"balance": 999}},
            {"preferred": True, "currency": "EUR", "balance": {"balance": 500.0}},
        ]
    })

    balance, currency = client.get_account_balance()

    assert balance == 500.0
    assert currency == "EUR"


def test_get_account_balance_raises_if_no_preferred_account():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={"accounts": [{"preferred": False}]})

    with pytest.raises(CapitalApiError):
        client.get_account_balance()


def test_open_position_success_extracts_deal_id_from_affected_deals():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-1"})
    session.get.return_value = _fake_response(json_body={
        "dealStatus": "ACCEPTED",
        "level": 91950.0,
        "affectedDeals": [{"dealId": "pos-123"}],
        "dealId": "order-999",  # id de l'ORDRE, jamais celui à utiliser (voir docstring)
    })

    result = client.open_position("BTCUSD", "SELL", 0.001)

    assert result["deal_id"] == "pos-123"
    assert result["level"] == 91950.0


def test_open_position_no_deal_reference_raises():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={})

    with pytest.raises(CapitalApiError):
        client.open_position("BTCUSD", "SELL", 0.001)


def test_open_position_rejected_status_raises():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-1"})
    session.get.return_value = _fake_response(json_body={"dealStatus": "REJECTED"})

    with pytest.raises(CapitalApiError):
        client.open_position("BTCUSD", "SELL", 0.001)


def test_open_position_accepted_but_no_affected_deals_raises():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-1"})
    session.get.return_value = _fake_response(json_body={"dealStatus": "ACCEPTED", "affectedDeals": []})

    with pytest.raises(CapitalApiError):
        client.open_position("BTCUSD", "SELL", 0.001)


def test_open_position_with_guaranteed_stop_includes_body_fields():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-1"})
    session.get.return_value = _fake_response(json_body={
        "dealStatus": "ACCEPTED", "affectedDeals": [{"dealId": "pos-1"}],
    })

    client.open_position("BTCUSD", "BUY", 0.001, guaranteed_stop=True, stop_distance=150.0)

    body = session.post.call_args.kwargs["json"]
    assert body["guaranteedStop"] is True
    assert body["stopDistance"] == 150.0


def test_close_position_full_sends_no_size():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-1"})
    session.get.return_value = _fake_response(json_body={"status": "CLOSED", "level": 91950.0, "date": "2026-08-27T10:00:00"})

    client.close_position("pos-1")

    assert session.delete.call_args.kwargs["json"] is None


def test_close_position_partial_sends_size():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-1"})
    session.get.return_value = _fake_response(json_body={"status": "CLOSED", "level": 91950.0, "date": "2026-08-27T10:00:00"})

    client.close_position("pos-1", size=0.0005)

    assert session.delete.call_args.kwargs["json"] == {"size": 0.0005}


def test_close_position_resolves_real_execution_price_and_date():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={"status": "CLOSED", "level": 91950.0, "date": "2026-08-27T10:00:00"})

    result = client.close_position("pos-1")

    session.get.assert_called_once()
    assert session.get.call_args[0][0].endswith("/confirms/ref-close-1")
    assert result["level"] == 91950.0
    assert result["executed_at"] == "2026-08-27T10:00:00"
    assert result["confirmation"]["level"] == 91950.0


def test_close_position_retries_on_stale_open_confirmation_then_succeeds(monkeypatch):
    # 28/08/2026 (voir docs/DECISIONS.md) : dealReference = "p_"+deal_id
    # n'est pas unique par transaction — un GET immédiat peut renvoyer la
    # confirmation PÉRIMÉE de l'ouverture d'origine (status="OPEN"),
    # observé sur 2 clôtures réelles sur 3. Doit retenter jusqu'à
    # status="CLOSED".
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.side_effect = [
        _fake_response(json_body={"status": "OPEN", "level": 100.0, "date": "2026-08-28T05:24:19"}),
        _fake_response(json_body={"status": "OPEN", "level": 100.0, "date": "2026-08-28T05:24:19"}),
        _fake_response(json_body={"status": "CLOSED", "level": 99.7, "date": "2026-08-28T11:00:55"}),
    ]
    sleeps = []
    monkeypatch.setattr("src.capital_client.time.sleep", lambda s: sleeps.append(s))

    result = client.close_position("pos-1")

    assert session.get.call_count == 3
    assert result["level"] == 99.7
    assert result["executed_at"] == "2026-08-28T11:00:55"
    assert len(sleeps) == 2  # une pause entre chaque tentative, jamais après la dernière


def test_close_position_gives_up_after_max_attempts_never_closed(monkeypatch):
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={"status": "OPEN", "level": 100.0, "date": "2026-08-28T05:24:19"})
    monkeypatch.setattr("src.capital_client.time.sleep", lambda s: None)

    result = client.close_position("pos-1")

    from src.capital_client import _CLOSE_CONFIRM_MAX_ATTEMPTS
    assert session.get.call_count == _CLOSE_CONFIRM_MAX_ATTEMPTS
    assert result == {"level": None, "executed_at": None, "confirmation": {"status": "OPEN", "level": 100.0, "date": "2026-08-28T05:24:19"}}


def test_close_position_forced_stale_open_confirmation_retries_then_returns_none(monkeypatch):
    # 28/08/2026 (voir docs/DECISIONS.md, demande explicite) : force le
    # scénario réellement observé (trade 14231/14239) plutôt que de
    # l'attendre — status ET horodatage identiques à l'OUVERTURE
    # d'origine, jamais status="CLOSED". Doit retenter puis abandonner
    # sur None, jamais renvoyer la valeur périmée.
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "p_pos-1"})
    stale_open_confirmation = {
        "date": "2026-08-27T22:05:17.839",  # horodatage de l'OUVERTURE d'origine
        "status": "OPEN", "dealStatus": "ACCEPTED", "epic": "ETHUSD",
        "affectedDeals": [{"dealId": "pos-1", "status": "OPENED"}],
        "level": 2511.76, "size": 0.1, "direction": "BUY",
    }
    session.get.return_value = _fake_response(json_body=stale_open_confirmation)
    sleeps = []
    monkeypatch.setattr("src.capital_client.time.sleep", lambda s: sleeps.append(s))

    result = client.close_position("pos-1", requested_at="2026-08-28T01:19:03.424")

    from src.capital_client import _CLOSE_CONFIRM_MAX_ATTEMPTS
    assert session.get.call_count == _CLOSE_CONFIRM_MAX_ATTEMPTS
    assert len(sleeps) == _CLOSE_CONFIRM_MAX_ATTEMPTS - 1
    assert result["level"] is None
    assert result["executed_at"] is None
    assert result["confirmation"] == stale_open_confirmation  # conservée pour diagnostic, jamais utilisée comme prix réel


def test_close_position_rejects_closed_confirmation_older_than_request():
    # Second discriminant, orthogonal a status="CLOSED" : une confirmation
    # "CLOSED" mais ANTERIEURE a la demande de cloture ne peut pas
    # decrire CETTE cloture (ex. cloture precedente d'une autre jambe).
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={
        "status": "CLOSED", "level": 100.0, "date": "2026-08-28T05:00:00",
    })

    result = client.close_position("pos-1", requested_at="2026-08-28T06:00:00", size=None)

    assert result["level"] is None
    assert result["executed_at"] is None


def test_close_position_accepts_closed_confirmation_newer_than_request():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={
        "status": "CLOSED", "level": 100.0, "date": "2026-08-28T06:00:00",
    })

    result = client.close_position("pos-1", requested_at="2026-08-28T05:00:00")

    assert result["level"] == 100.0


def test_close_position_without_requested_at_skips_freshness_check():
    # Compatibilite arriere explicite : aucun appelant existant ne casse.
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={
        "status": "CLOSED", "level": 100.0, "date": "2020-01-01T00:00:00",
    })

    result = client.close_position("pos-1")

    assert result["level"] == 100.0


def test_close_position_missing_confirmation_date_treated_as_not_fresh(monkeypatch):
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={"status": "CLOSED", "level": 100.0})
    monkeypatch.setattr("src.capital_client.time.sleep", lambda s: None)

    result = client.close_position("pos-1", requested_at="2026-08-28T05:00:00")

    assert result["level"] is None


def test_close_position_unparseable_confirmation_date_treated_as_not_fresh(monkeypatch):
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(json_body={"status": "CLOSED", "level": 100.0, "date": "not-a-date"})
    monkeypatch.setattr("src.capital_client.time.sleep", lambda s: None)

    result = client.close_position("pos-1", requested_at="2026-08-28T05:00:00")

    assert result["level"] is None


def test_close_position_without_deal_reference_returns_none_fields():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"status": "closed"})

    result = client.close_position("pos-1")

    session.get.assert_not_called()
    assert result == {"level": None, "executed_at": None, "confirmation": None}


def test_close_position_confirmation_failure_is_best_effort():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-close-1"})
    session.get.return_value = _fake_response(status_ok=False)

    result = client.close_position("pos-1")

    assert result == {"level": None, "executed_at": None, "confirmation": None}


def test_update_position_stop_uses_put():
    client, session = _logged_in_client()
    session.put.return_value = _fake_response(json_body={"status": "updated"})

    client.update_position_stop("pos-1", 92000.0)

    session.put.assert_called_once()
    assert session.put.call_args.kwargs["json"] == {"stopLevel": 92000.0}


def test_update_position_stop_includes_guaranteed_stop_flag_when_true():
    # Sans ce champ, Capital.com rejette la mise à jour d'une position
    # ouverte avec stop garanti (error.vallidation.guaranteed-stop-loss.
    # required) — bug réel trouvé en production le 20/08/2026, voir
    # docs/DECISIONS.md.
    client, session = _logged_in_client()
    session.put.return_value = _fake_response(json_body={"status": "updated"})

    client.update_position_stop("pos-1", 92000.0, guaranteed_stop=True)

    assert session.put.call_args.kwargs["json"] == {"stopLevel": 92000.0, "guaranteedStop": True}


def _stoploss_error_response(error_code_body: str):
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("400 Client Error")
    resp.text = f'{{"errorCode":"{error_code_body}"}}'
    return resp


def test_update_position_stop_retries_once_with_broker_disclosed_boundary():
    # 5563 echecs mesures en production (voir docs/DECISIONS.md, refonte
    # H1-H5 point E) — le broker divulgue la valeur limite exacte dans le
    # message d'erreur ; un seul reessai avec CETTE valeur, jamais une
    # formule locale.
    client, session = _logged_in_client()
    session.put.side_effect = [
        _stoploss_error_response("error.invalid.stoploss.minvalue: 158.894"),
        _fake_response(json_body={"status": "updated"}),
    ]

    result = client.update_position_stop("pos-1", 159.0)

    assert result == {"status": "updated"}
    assert session.put.call_count == 2
    assert session.put.call_args_list[1].kwargs["json"] == {"stopLevel": 158.894}


def test_update_position_stop_retry_preserves_guaranteed_stop_flag():
    client, session = _logged_in_client()
    session.put.side_effect = [
        _stoploss_error_response("error.invalid.stoploss.maxvalue: 4503.06"),
        _fake_response(json_body={"status": "updated"}),
    ]

    client.update_position_stop("pos-1", 4520.0, guaranteed_stop=True)

    assert session.put.call_args_list[1].kwargs["json"] == {"stopLevel": 4503.06, "guaranteedStop": True}


def test_update_position_stop_no_retry_when_error_unrelated_to_stoploss_bounds():
    client, session = _logged_in_client()
    session.put.return_value = _stoploss_error_response("error.vallidation.guaranteed-stop-loss.required")

    with pytest.raises(CapitalApiError):
        client.update_position_stop("pos-1", 159.0)
    assert session.put.call_count == 1  # jamais de reessai sur une erreur non reconnue


def test_update_position_stop_retry_gives_up_after_one_attempt():
    # Reessai borne (jamais une boucle) : si le second essai echoue aussi
    # (bande encore deplacee entre-temps), l'exception est propagee.
    client, session = _logged_in_client()
    session.put.side_effect = [
        _stoploss_error_response("error.invalid.stoploss.minvalue: 158.894"),
        _stoploss_error_response("error.invalid.stoploss.minvalue: 159.1"),
    ]

    with pytest.raises(CapitalApiError):
        client.update_position_stop("pos-1", 159.0)
    assert session.put.call_count == 2


def test_update_position_stop_retry_refused_when_it_would_widen_stop_long():
    # Invariant #5 : jamais elargi, meme via ce garde-fou — position
    # longue, stop actuel a 100.0, la valeur divulguee (95.0) serait un
    # elargissement (plus loin du prix) -> exception propagee, pas de
    # reessai tente.
    client, session = _logged_in_client()
    session.put.return_value = _stoploss_error_response("error.invalid.stoploss.minvalue: 95.0")

    with pytest.raises(CapitalApiError):
        client.update_position_stop("pos-1", 99.0, direction="long", current_stop_level=100.0)
    assert session.put.call_count == 1


def test_update_position_stop_retry_refused_when_it_would_widen_stop_short():
    client, session = _logged_in_client()
    session.put.return_value = _stoploss_error_response("error.invalid.stoploss.maxvalue: 110.0")

    with pytest.raises(CapitalApiError):
        client.update_position_stop("pos-1", 105.0, direction="short", current_stop_level=100.0)
    assert session.put.call_count == 1


def test_update_position_stop_retry_allowed_when_it_tightens_stop():
    client, session = _logged_in_client()
    session.put.side_effect = [
        _stoploss_error_response("error.invalid.stoploss.minvalue: 101.0"),
        _fake_response(json_body={"status": "updated"}),
    ]

    result = client.update_position_stop("pos-1", 99.0, direction="long", current_stop_level=100.0)
    assert result == {"status": "updated"}
    assert session.put.call_args_list[1].kwargs["json"] == {"stopLevel": 101.0}


_NOT_YET_ACTIVE_ACCOUNTS = _fake_response(json_body={
    "accounts": [
        {"accountId": "some-other-account", "preferred": True},
        {"accountId": "327614560537498782", "preferred": False},
    ]
})


def test_switch_account_targets_explicit_account_id():
    # Incident réel du 20/08/2026 (voir docs/DECISIONS.md) : le compte
    # "préféré" est un état partagé entre toutes les clés API d'un même
    # identifiant, peut basculer silencieusement — ne jamais en dépendre.
    client, session = _logged_in_client()
    session.get.return_value = _NOT_YET_ACTIVE_ACCOUNTS
    session.put.return_value = _fake_response(json_body={"status": "SUCCESS"}, headers={})

    result = client.switch_account("327614560537498782")

    assert result == {"status": "SUCCESS"}
    session.put.assert_called_once()
    call = session.put.call_args
    assert call.args[0] == "https://api.example.com/api/v1/session"
    assert call.kwargs["json"] == {"accountId": "327614560537498782"}
    assert call.kwargs["headers"]["CST"] == "c"


def test_switch_account_updates_tokens_when_response_includes_new_ones():
    client, session = _logged_in_client()
    session.get.return_value = _NOT_YET_ACTIVE_ACCOUNTS
    session.put.return_value = _fake_response(
        json_body={"status": "SUCCESS"}, headers={"CST": "new-cst", "X-SECURITY-TOKEN": "new-sec"},
    )

    client.switch_account("327614560537498782")

    session.get.return_value = _fake_response(json_body={"ok": True})
    client.get("/accounts")
    headers_used = session.get.call_args.kwargs["headers"]
    assert headers_used["CST"] == "new-cst"
    assert headers_used["X-SECURITY-TOKEN"] == "new-sec"


def test_switch_account_keeps_existing_tokens_when_response_has_none():
    client, session = _logged_in_client()
    session.get.return_value = _NOT_YET_ACTIVE_ACCOUNTS
    session.put.return_value = _fake_response(json_body={"status": "SUCCESS"}, headers={})

    client.switch_account("327614560537498782")

    session.get.return_value = _fake_response(json_body={"ok": True})
    client.get("/accounts")
    headers_used = session.get.call_args.kwargs["headers"]
    assert headers_used["CST"] == "c"  # tokens de login() conservés
    assert headers_used["X-SECURITY-TOKEN"] == "s"


def test_switch_account_skips_put_when_already_active():
    # Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : si le compte
    # "préféré" partagé coïncide déjà avec la cible au moment de login(),
    # PUT /session échoue avec error.not-different.accountId — vérifié
    # ici via GET /accounts AVANT toute tentative, aucun appel PUT ne
    # doit être fait dans ce cas.
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={
        "accounts": [
            {"accountId": "other-account", "preferred": False},
            {"accountId": "327614560537498782", "preferred": True},
        ]
    })

    result = client.switch_account("327614560537498782")

    assert result == {"accountId": "327614560537498782", "alreadyActive": True}
    session.put.assert_not_called()


def test_switch_account_puts_when_target_not_yet_preferred():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={
        "accounts": [
            {"accountId": "327614560537498782", "preferred": True},
            {"accountId": "target-account", "preferred": False},
        ]
    })
    session.put.return_value = _fake_response(json_body={"status": "SUCCESS"}, headers={})

    result = client.switch_account("target-account")

    assert result == {"status": "SUCCESS"}
    session.put.assert_called_once()
    assert session.put.call_args.kwargs["json"] == {"accountId": "target-account"}


def test_switch_account_puts_when_accounts_list_empty():
    # Défense en profondeur : si /accounts ne renvoie rien d'exploitable,
    # ne jamais présumer "déjà actif" silencieusement — tente le PUT
    # normalement plutôt que de sauter une étape sur une base incertaine.
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={"accounts": []})
    session.put.return_value = _fake_response(json_body={"status": "SUCCESS"}, headers={})

    client.switch_account("327614560537498782")

    session.put.assert_called_once()


def test_switch_account_before_login_raises():
    client, _ = _client_with_session()
    with pytest.raises(CapitalApiError):
        client.switch_account("327614560537498782")


def test_switch_account_http_error_wrapped():
    client, session = _logged_in_client()
    session.get.return_value = _NOT_YET_ACTIVE_ACCOUNTS
    session.put.return_value = _fake_response(status_ok=False)
    with pytest.raises(CapitalApiError):
        client.switch_account("327614560537498782")


def test_get_prices_passes_resolution_and_max():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={"prices": []})

    client.get_prices("EURUSD", resolution="HOUR", max_bars=14)

    assert session.get.call_args.kwargs["params"] == {"resolution": "HOUR", "max": 14}


def test_get_open_positions_returns_list():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={"positions": [{"a": 1}]})

    assert client.get_open_positions() == [{"a": 1}]


def test_place_limit_order_success_extracts_deal_id():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-2"})
    session.get.return_value = _fake_response(json_body={
        "dealStatus": "ACCEPTED", "level": 1.15112, "affectedDeals": [{"dealId": "wo-1"}],
    })

    result = client.place_limit_order("EURUSD", "BUY", 100, level=1.15112)

    called_path = session.post.call_args.args[0]
    assert called_path.endswith("/workingorders")
    body = session.post.call_args.kwargs["json"]
    assert body["type"] == "LIMIT"
    assert body["level"] == 1.15112
    assert result["deal_id"] == "wo-1"


def test_place_limit_order_with_guaranteed_stop():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-2"})
    session.get.return_value = _fake_response(json_body={
        "dealStatus": "ACCEPTED", "affectedDeals": [{"dealId": "wo-1"}],
    })

    client.place_limit_order("EURUSD", "BUY", 100, level=1.15112, guaranteed_stop=True, stop_distance=0.005)

    body = session.post.call_args.kwargs["json"]
    assert body["guaranteedStop"] is True
    assert body["stopDistance"] == 0.005


def test_place_limit_order_rejected_raises():
    client, session = _logged_in_client()
    session.post.return_value = _fake_response(json_body={"dealReference": "ref-2"})
    session.get.return_value = _fake_response(json_body={"dealStatus": "REJECTED"})

    with pytest.raises(CapitalApiError):
        client.place_limit_order("EURUSD", "BUY", 100, level=1.15112)


def test_cancel_working_order_calls_correct_path():
    client, session = _logged_in_client()
    session.delete.return_value = _fake_response(json_body={"dealReference": "ref-3"})

    client.cancel_working_order("wo-1")

    called_path = session.delete.call_args.args[0]
    assert called_path.endswith("/workingorders/wo-1")


def test_get_working_orders_returns_list():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={"workingOrders": [{"a": 1}]})

    assert client.get_working_orders() == [{"a": 1}]


def test_get_market_snapshot_calls_correct_path():
    client, session = _logged_in_client()
    session.get.return_value = _fake_response(json_body={"snapshot": {}})

    client.get_market_snapshot("EURUSD")

    called_path = session.get.call_args.args[0]
    assert called_path.endswith("/markets/EURUSD")
