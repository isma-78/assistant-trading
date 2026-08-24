"""
Tests d'intégration légers pour circuit_breaker_store.py (I/O + DB réelle
temporaire, pas de mock) — même niveau d'exigence qu'envelope_store.py,
pas les 100% de circuit_breaker.py (logique pure déjà couverte à part).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.circuit_breaker_store import (
    check_channel_inactivity,
    clear_breaker,
    get_active_global_block,
    get_closed_trades_r,
    get_open_risk_eur,
    get_unhandled_stop_urgence_event_id,
    is_asset_blocked,
    mark_stop_urgence_handled,
    record_api_result,
    trigger_manual_pause,
    trigger_stop_urgence,
)
from src.db import connection_scope, get_connection, init_db

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _insert_closed_trade(db_path, actif, source, ferme_at, r_multiple, risque_eur=10.0):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, ferme_at, r_multiple_total, statut) "
            "VALUES (?, ?, 'demo', 'long', 0.01, 1.0, 1.0, ?, 2.0, ?, ?, ?, 'ferme')",
            (source, actif, risque_eur, ferme_at, ferme_at, r_multiple),
        )


def _insert_open_trade(db_path, actif, source, risque_eur=10.0):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, statut) "
            "VALUES (?, ?, 'demo', 'long', 0.01, 1.0, 1.0, ?, 2.0, ?, 'ouvert')",
            (source, actif, risque_eur, NOW.isoformat()),
        )


def _insert_raw_message(db_path, channel, received_at, msg_id=1):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (?, ?, ?, 'texte', 'signal')",
            (msg_id, channel, received_at),
        )


# ---------------------------------------------------------------------------
# Lecture d'historique / exposition
# ---------------------------------------------------------------------------

def test_get_closed_trades_r_filters_by_normalized_source(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_closed_trade(db_path, "EURUSD", "hypothesis", "2026-08-20T10:00:00+00:00", -1.0)
    _insert_closed_trade(db_path, "EURUSD", "-1002481537588", "2026-08-20T10:00:00+00:00", 2.0)  # station x, id brut du canal

    hypothesis_r = get_closed_trades_r(db_path, "EURUSD", "hypothesis")
    stationx_r = get_closed_trades_r(db_path, "EURUSD", "stationx")

    assert hypothesis_r == [("2026-08-20T10:00:00+00:00", -1.0)]
    assert stationx_r == [("2026-08-20T10:00:00+00:00", 2.0)]


def test_get_open_risk_eur_sums_open_trades_for_source(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_open_trade(db_path, "GOLD", "stationx", risque_eur=10.0)
    _insert_open_trade(db_path, "GOLD", "stationx", risque_eur=15.0)
    _insert_open_trade(db_path, "GOLD", "hypothesis", risque_eur=99.0)

    assert get_open_risk_eur(db_path, "GOLD", "stationx") == 25.0
    assert get_open_risk_eur(db_path, "GOLD", "hypothesis") == 99.0


# ---------------------------------------------------------------------------
# is_asset_blocked — trajectoires principales
# ---------------------------------------------------------------------------

def test_is_asset_blocked_false_when_nothing_triggered(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    blocked, reason = is_asset_blocked(db_path, "EURUSD", "stationx", NOW)
    assert blocked is False
    assert reason == ""


@patch("src.circuit_breaker_store.send_notification", return_value=True)
def test_is_asset_blocked_true_on_new_day_breaker_and_persists_event(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_closed_trade(db_path, "EURUSD", "stationx", NOW.isoformat(), -2.5)

    blocked, reason = is_asset_blocked(db_path, "EURUSD", "stationx", NOW, bot_token="t", chat_id="c")
    assert blocked is True
    assert "day_r" in reason
    mock_notify.assert_called_once()

    with connection_scope(db_path) as conn:
        events = conn.execute(
            "SELECT * FROM circuit_breaker_events WHERE actif = ? AND breaker_type = 'day_r'", ("EURUSD",)
        ).fetchall()
    assert len(events) == 1

    # Deuxième appel : déjà déclenché aujourd'hui -> toujours bloqué, pas de doublon
    blocked_again, _ = is_asset_blocked(db_path, "EURUSD", "stationx", NOW, bot_token="t", chat_id="c")
    assert blocked_again is True
    mock_notify.assert_called_once()  # pas de deuxième notification


@patch("src.circuit_breaker_store.send_notification", return_value=True)
def test_is_asset_blocked_new_day_breaker_records_causal_analysis(mock_notify, tmp_path):
    # Moteur d'analyse causale (§3.11, 24/08/2026) : câblé en lecture
    # seule après record_trigger, ne doit jamais changer la décision de
    # blocage (déjà vérifiée par le test ci-dessus) — vérifie ici
    # uniquement l'effet de bord : une ligne causal_analysis_log créée.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_closed_trade(db_path, "EURUSD", "stationx", NOW.isoformat(), -2.5)

    is_asset_blocked(db_path, "EURUSD", "stationx", NOW, bot_token="t", chat_id="c")

    with connection_scope(db_path) as conn:
        rows = conn.execute("SELECT * FROM causal_analysis_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["declencheur"] == "day_r:EURUSD:stationx"


def test_is_asset_blocked_decision_unaffected_if_causal_analysis_fails(tmp_path):
    # Défense en profondeur : même si l'analyse causale levait une
    # exception non interceptée en interne (record_causal_analysis est
    # déjà fail-safe, voir test_causal_analyzer.py — ce test simule le
    # cas où ce filet manquerait), le point d'appel dans
    # is_asset_blocked absorbe l'erreur — la décision de blocage
    # (déjà déterminée par circuit_breaker.py) n'en dépend jamais.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_closed_trade(db_path, "EURUSD", "stationx", NOW.isoformat(), -2.5)

    with patch("src.circuit_breaker_store.causal_analyzer.record_causal_analysis", side_effect=RuntimeError("panne")):
        blocked, reason = is_asset_blocked(db_path, "EURUSD", "stationx", NOW)
    assert blocked is True
    assert "day_r" in reason


def test_is_asset_blocked_true_when_global_block_active(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trigger_stop_urgence(db_path, "ismael")
    blocked, reason = is_asset_blocked(db_path, "EURUSD", "stationx", NOW)
    assert blocked is True
    assert reason == "global:stop_urgence"


def test_is_asset_blocked_true_on_manual_pause_all_sources(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trigger_manual_pause(db_path, "EURUSD", "ismael")
    blocked_x, _ = is_asset_blocked(db_path, "EURUSD", "stationx", NOW)
    blocked_h, _ = is_asset_blocked(db_path, "EURUSD", "hypothesis", NOW)
    assert blocked_x is True and blocked_h is True


def test_is_asset_blocked_week_latched_ignores_live_recovery(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    # Semaine catastrophique -> déclenche week_r
    _insert_closed_trade(db_path, "GOLD", "stationx", NOW.isoformat(), -6.0)
    blocked_first, reason_first = is_asset_blocked(db_path, "GOLD", "stationx", NOW)
    assert blocked_first is True
    assert "week_r" in reason_first

    # Un peu plus tard, même journée suivante : R en direct est redevenu > seuil
    # mais le week_r reste latché (reprise manuelle uniquement, §2.7)
    later = NOW + timedelta(hours=1)
    blocked_second, reason_second = is_asset_blocked(db_path, "GOLD", "stationx", later)
    assert blocked_second is True
    assert "week_r" in reason_second


# ---------------------------------------------------------------------------
# clear_breaker (/reprendre)
# ---------------------------------------------------------------------------

def test_clear_breaker_asset_scoped(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trigger_manual_pause(db_path, "GOLD", "ismael")
    cleared = clear_breaker(db_path, "GOLD", "ismael")
    assert cleared == 1
    blocked, _ = is_asset_blocked(db_path, "GOLD", "stationx", NOW)
    assert blocked is False


def test_clear_breaker_global_scoped(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trigger_stop_urgence(db_path, "ismael")
    cleared = clear_breaker(db_path, None, "ismael")
    assert cleared == 1
    assert get_active_global_block(db_path) is None


# ---------------------------------------------------------------------------
# Surcouche anomalie système
# ---------------------------------------------------------------------------

def test_record_api_result_resets_on_success(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert record_api_result(db_path, "executor", success=False) == 1
    assert record_api_result(db_path, "executor", success=False) == 2
    assert record_api_result(db_path, "executor", success=True) == 0


@patch("src.circuit_breaker_store.send_notification", return_value=True)
def test_record_api_result_triggers_global_pause_at_threshold(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    record_api_result(db_path, "executor", success=False, bot_token="t", chat_id="c")
    record_api_result(db_path, "executor", success=False, bot_token="t", chat_id="c")
    record_api_result(db_path, "executor", success=False, bot_token="t", chat_id="c")
    assert get_active_global_block(db_path) == "api_errors"
    mock_notify.assert_called_once()


@patch("src.circuit_breaker_store.send_notification", return_value=True)
def test_breadth_pause_triggers_at_five_distinct_assets(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assets = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD"]
    for actif in assets:
        _insert_closed_trade(db_path, actif, "stationx", NOW.isoformat(), -2.5)

    for actif in assets:
        is_asset_blocked(db_path, actif, "stationx", NOW, bot_token="t", chat_id="c")

    assert get_active_global_block(db_path) == "breadth"


def test_check_channel_inactivity_no_alert_when_channel_active(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_raw_message(db_path, "station_x", NOW.isoformat())
    check_channel_inactivity(db_path, NOW, bot_token=None, chat_id=None)
    with connection_scope(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM circuit_breaker_events WHERE breaker_type = 'channel_inactive'"
        ).fetchone()["n"]
    assert count == 0


def test_check_channel_inactivity_alerts_once_per_day(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_raw_message(db_path, "station_x", (NOW - timedelta(days=8)).isoformat())

    check_channel_inactivity(db_path, NOW, bot_token=None, chat_id=None)
    assert get_active_global_block(db_path) is None  # channel_inactive n'est jamais bloquant
    with connection_scope(db_path) as conn:
        count_first = conn.execute(
            "SELECT COUNT(*) AS n FROM circuit_breaker_events WHERE breaker_type = 'channel_inactive'"
        ).fetchone()["n"]
    assert count_first == 1

    check_channel_inactivity(db_path, NOW + timedelta(hours=2), bot_token=None, chat_id=None)
    with connection_scope(db_path) as conn:
        count_second = conn.execute(
            "SELECT COUNT(*) AS n FROM circuit_breaker_events WHERE breaker_type = 'channel_inactive'"
        ).fetchone()["n"]
    assert count_second == 1  # pas de doublon le même jour


def test_check_channel_inactivity_ignores_trend_strategy_synthetic_messages(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    # Le Flux B est actif (messages synthétiques récents) mais Station X est mort depuis 10 jours
    _insert_raw_message(db_path, "station_x", (NOW - timedelta(days=10)).isoformat(), msg_id=1)
    _insert_raw_message(db_path, "trend_strategy", NOW.isoformat(), msg_id=2)

    check_channel_inactivity(db_path, NOW, bot_token=None, chat_id=None)
    with connection_scope(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM circuit_breaker_events WHERE breaker_type = 'channel_inactive'"
        ).fetchone()["n"]
    assert count == 1  # doit alerter malgré l'activité Flux B


# ---------------------------------------------------------------------------
# stop_urgence — suivi "déjà traité" par process
# ---------------------------------------------------------------------------

def test_stop_urgence_handled_tracking(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_unhandled_stop_urgence_event_id(db_path, "executor") is None

    event_id = trigger_stop_urgence(db_path, "ismael")
    unhandled = get_unhandled_stop_urgence_event_id(db_path, "executor")
    assert unhandled == event_id

    mark_stop_urgence_handled(db_path, "executor", event_id)
    assert get_unhandled_stop_urgence_event_id(db_path, "executor") is None

    # Un autre process (trend_executor) n'a pas encore traité cet événement
    assert get_unhandled_stop_urgence_event_id(db_path, "trend_executor") == event_id


def test_stop_urgence_new_activation_after_reprendre_is_unhandled_again(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    first_id = trigger_stop_urgence(db_path, "ismael")
    mark_stop_urgence_handled(db_path, "executor", first_id)
    clear_breaker(db_path, None, "ismael")

    second_id = trigger_stop_urgence(db_path, "ismael")
    assert second_id != first_id
    assert get_unhandled_stop_urgence_event_id(db_path, "executor") == second_id
