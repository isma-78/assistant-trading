from unittest.mock import MagicMock, patch

from src.circuit_breaker_store import get_active_global_block, is_asset_blocked
from src.control_bot import (
    _process_update,
    format_etat,
    handle_command,
    parse_command,
)
from src.db import connection_scope, init_db

from datetime import datetime, timezone

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------

def test_parse_command_simple():
    assert parse_command("/etat") == ("etat", None)


def test_parse_command_with_argument_uppercased():
    assert parse_command("/pause gold") == ("pause", "GOLD")


def test_parse_command_not_a_command_returns_none():
    assert parse_command("bonjour") is None


def test_parse_command_slash_alone_returns_none():
    assert parse_command("/") is None


def test_parse_command_with_trailing_whitespace_argument():
    assert parse_command("/pause   ") == ("pause", None)


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------

def test_handle_command_etat_empty_system(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "etat", None)
    assert "Aucune" in reply
    assert "Aucun" in reply


def test_handle_command_pause_global_blocks_all_assets(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "pause", None, triggered_by="ismael")
    assert "globale" in reply.lower()
    blocked, reason = is_asset_blocked(db_path, "EURUSD", "stationx", NOW)
    assert blocked is True
    assert reason == "global:manual_pause"


def test_handle_command_pause_asset_scoped(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    handle_command(db_path, "pause", "GOLD", triggered_by="ismael")
    blocked_gold, _ = is_asset_blocked(db_path, "GOLD", "stationx", NOW)
    blocked_eurusd, _ = is_asset_blocked(db_path, "EURUSD", "stationx", NOW)
    assert blocked_gold is True
    assert blocked_eurusd is False


def test_handle_command_reprendre_clears_pause(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    handle_command(db_path, "pause", "GOLD", triggered_by="ismael")
    reply = handle_command(db_path, "reprendre", "GOLD", triggered_by="ismael")
    assert "1" in reply
    blocked, _ = is_asset_blocked(db_path, "GOLD", "stationx", NOW)
    assert blocked is False


def test_handle_command_stop_urgence_sets_global_block(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "stop_urgence", None, triggered_by="ismael")
    assert "URGENCE" in reply
    assert get_active_global_block(db_path) == "stop_urgence"


def test_handle_command_unknown_lists_available_commands(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "bidule", None)
    assert "inconnue" in reply.lower()
    assert "/etat" in reply
    assert "/dashboard" in reply


@patch("src.control_bot.send_document", return_value=True)
def test_handle_command_dashboard_sends_document_and_returns_empty_reply(mock_send_document, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "dashboard", None, bot_token="tok", chat_id="chat")

    assert reply == ""
    mock_send_document.assert_called_once()
    args, kwargs = mock_send_document.call_args
    assert args[0] == "tok"
    assert args[1] == "chat"
    sent_file_path = args[2]
    import os
    assert not os.path.exists(sent_file_path), "le fichier temporaire doit être supprimé après envoi"


@patch("src.control_bot.send_document", return_value=False)
def test_handle_command_dashboard_send_failure_returns_error_text(mock_send_document, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "dashboard", None, bot_token="tok", chat_id="chat")
    assert "Échec" in reply


def test_handle_command_dashboard_without_credentials_returns_fallback_text(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    reply = handle_command(db_path, "dashboard", None)  # pas de bot_token/chat_id
    assert "indisponible" in reply.lower()


@patch("src.control_bot.send_document", return_value=True)
@patch("src.control_bot.send_notification", return_value=True)
def test_process_update_dashboard_does_not_double_send_text(mock_notify, mock_send_document, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/dashboard"}}
    _process_update(db_path, update, authorized_chat_id="12345", bot_token="tok")
    mock_send_document.assert_called_once()
    mock_notify.assert_not_called()  # le document envoyé sert déjà de réponse


def test_format_etat_shows_open_trade_and_envelope(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_reel, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('stationx', 'GOLD', 'demo', 'short', 0.01, 2000.0, 2010.0, 2010.0, 10.0, 2.0, "
            "'2026-08-20T00:00:00Z', 'ouvert')"
        )
        conn.execute(
            "INSERT INTO envelopes (actif, mode, source, capital_initial, capital_courant, created_at, updated_at) "
            "VALUES ('GOLD', 'demo', 'stationx', 500.0, 490.0, '2026-08-20T00:00:00Z', '2026-08-20T00:00:00Z')"
        )
    reply = format_etat(db_path)
    assert "GOLD" in reply
    assert "short" in reply
    assert "490.0" in reply


# ---------------------------------------------------------------------------
# _process_update — authentification par chat_id
# ---------------------------------------------------------------------------

@patch("src.control_bot.send_notification", return_value=True)
def test_process_update_authorized_chat_executes_command(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/etat"}}
    _process_update(db_path, update, authorized_chat_id="12345", bot_token="tok")
    mock_notify.assert_called_once()


@patch("src.control_bot.send_notification", return_value=True)
def test_process_update_unauthorized_chat_ignored(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    update = {"message": {"chat": {"id": 99999}, "text": "/stop_urgence"}}
    _process_update(db_path, update, authorized_chat_id="12345", bot_token="tok")
    mock_notify.assert_not_called()
    assert get_active_global_block(db_path) is None


def test_process_update_non_text_message_ignored(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    update = {"message": {"chat": {"id": 12345}}}  # pas de "text" (ex: photo)
    _process_update(db_path, update, authorized_chat_id="12345", bot_token="tok")  # ne lève rien


def test_process_update_no_message_ignored(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _process_update(db_path, {"update_id": 1}, authorized_chat_id="12345", bot_token="tok")
