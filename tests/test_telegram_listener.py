"""
Tests de telegram_listener.process_message — routage classification →
extraction → persistance → notification, sur des exemples réels du canal.
Aucune dépendance Telethon : ces tests ne couvrent que la logique métier
pure (run_listener, câblage Telethon, est hors périmètre des tests
unitaires — nécessite une session réelle, voir docs/DECISIONS.md).
"""

from unittest.mock import patch

from src.db import get_connection, init_db
from src.message_classifier import MessageCategory
from src.telegram_listener import process_message

SIGNAL_STRUCTURED = "🔴 JE VENDS XAUUSD à 4367\n🎯 TP1 : 4364\n🎯 TP2 : 4357\n🎯 TP3 : Ouvert\n🔒 SL : 4370"
SIGNAL_ALERT = "VENTE XAUUSD NOW !"
MATINALE = (
    "Bonjour à tous ! C'est reparti pour un point marché sur la Matinale.\n\n"
    "✅ Du côté du Bitcoin en Daily, le prix évolue actuellement autour des 62 997 $. "
    "Le Bitcoin reste donc sous pression en Daily et les acheteurs doivent rapidement "
    "défendre cette zone basse pour éviter une nouvelle extension de la correction. "
    "Sentiment baissier.\n\n"
    "✅ Du côté du Gold en Daily, le prix évolue actuellement autour des 4 335 $. "
    "Malgré ce repli, la structure de fond reste constructive. "
    "Le Gold reste donc solide en Daily, mais le rejet des 4 447 $ appelle désormais "
    "à davantage de prudence. Sentiment baissier.\n\n"
    "✅ Bonne journée de trading à tous !"
)
SUIVI_TP1 = "TP1 TOUCHÉ 🔥 +30 PIPS 🟢"
AUTRE_BILAN = "Bilan trading du jour : +2,3R ✅"


def _row_counts(db_path, table):
    conn = get_connection(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        conn.close()


@patch("src.telegram_listener.send_notification")
def test_signal_structured_inserts_raw_message_and_signal(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    category = process_message(
        db_path, "station_x", 101, None, SIGNAL_STRUCTURED,
        received_at="2026-08-16T08:00:00Z", bot_token="tok", chat_id="42",
    )

    assert category == MessageCategory.SIGNAL
    assert _row_counts(db_path, "raw_messages") == 1
    assert _row_counts(db_path, "signals") == 1

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM signals").fetchone()
        assert row["actif"] == "GOLD"
        assert row["sens"] == "short"
        assert row["entree_min"] == 4367.0
        assert row["entree_max"] == 4367.0
        assert row["stop_loss"] == 4370.0
        assert row["tp1"] == 4364.0
        assert row["tp3"] is None
        assert row["statut"] == "a_valider"
        assert row["confiance"] == 1.0
    finally:
        conn.close()

    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][0] == "tok"
    assert mock_notify.call_args[0][1] == "42"


@patch("src.telegram_listener.send_notification")
def test_incomplete_signal_is_rejected_status(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    process_message(db_path, "station_x", 102, None, SIGNAL_ALERT, bot_token="tok", chat_id="42")

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM signals").fetchone()
        assert row["statut"] == "rejete"
        assert row["raison_rejet"] == "extraction_incomplete"
        assert row["confiance"] == 0.0
    finally:
        conn.close()


@patch("src.telegram_listener.send_notification")
def test_matinale_inserts_one_summary_per_asset_and_flags_contradiction(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    category = process_message(db_path, "station_x", 103, None, MATINALE, bot_token="tok", chat_id="42")

    assert category == MessageCategory.MATINALE
    assert _row_counts(db_path, "matinale_summaries") == 2

    conn = get_connection(db_path)
    try:
        gold = conn.execute(
            "SELECT * FROM matinale_summaries WHERE raw_asset_mention = 'Gold'"
        ).fetchone()
        assert gold["actif"] == "GOLD"
        assert gold["biais_corps"] == "haussier"
        assert gold["sentiment_tag"] == "baissier"
        assert gold["contradiction_detectee"] == 1
    finally:
        conn.close()

    assert mock_notify.call_count == 2


@patch("src.telegram_listener.send_notification")
def test_matinale_contradiction_notified_even_with_audit_all_false(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    process_message(
        db_path, "station_x", 104, None, MATINALE,
        bot_token="tok", chat_id="42", audit_all=False,
    )

    # Bitcoin (pas de contradiction) ne doit pas être notifié, Gold
    # (contradiction) doit l'être malgré audit_all=False (§7.2).
    assert mock_notify.call_count == 1
    assert "Contradiction" in mock_notify.call_args[0][2]


@patch("src.telegram_listener.send_notification")
def test_suivi_inserts_event_linked_to_reply(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    category = process_message(db_path, "station_x", 105, 99, SUIVI_TP1)

    assert category == MessageCategory.SUIVI
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM suivi_events").fetchone()
        assert row["event"] == "tp1_hit"
        assert row["pips"] == 30.0
        assert row["reply_to_msg_id"] == 99
    finally:
        conn.close()
    mock_notify.assert_not_called()


def test_autre_only_archives_raw_message(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    category = process_message(db_path, "station_x", 106, None, AUTRE_BILAN)

    assert category == MessageCategory.AUTRE
    assert _row_counts(db_path, "raw_messages") == 1
    assert _row_counts(db_path, "signals") == 0
    assert _row_counts(db_path, "matinale_summaries") == 0
    assert _row_counts(db_path, "suivi_events") == 0


def test_duplicate_message_is_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    process_message(db_path, "station_x", 107, None, SIGNAL_STRUCTURED)
    process_message(db_path, "station_x", 107, None, SIGNAL_STRUCTURED)

    assert _row_counts(db_path, "raw_messages") == 1
    assert _row_counts(db_path, "signals") == 1


def test_no_notification_sent_without_bot_credentials(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with patch("src.telegram_listener.send_notification") as mock_notify:
        process_message(db_path, "station_x", 108, None, SIGNAL_STRUCTURED)
        mock_notify.assert_not_called()
