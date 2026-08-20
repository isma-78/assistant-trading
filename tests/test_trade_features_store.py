"""
Tests de trade_features_store — collecte de la variable #1 du §3.8
(alignement avec le biais de la Matinale). compute_align_matinale() est
pure et testée à 100% ; get_latest_matinale_biais/record_align_matinale
sont testées avec une base SQLite temporaire réelle.
"""

from src.db import connection_scope, get_connection, init_db
from src.trade_features_store import (
    compute_align_matinale,
    get_latest_matinale_biais,
    record_align_matinale,
    record_align_matinale_for_trade,
)


_next_msg_id = iter(range(1, 10_000))


def _insert_matinale_summary(db_path, actif, sentiment_tag, published_at):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (?, 'station_x', ?, 'texte', 'matinale')",
            (next(_next_msg_id), published_at),
        ).lastrowid
        conn.execute(
            "INSERT INTO matinale_summaries "
            "(raw_message_id, raw_asset_mention, actif, biais_corps, sentiment_tag, contradiction_detectee, published_at) "
            "VALUES (?, ?, ?, 'indetermine', ?, 0, ?)",
            (raw_id, actif, actif, sentiment_tag, published_at),
        )


def _insert_dummy_trade(db_path):
    with connection_scope(db_path) as conn:
        return conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at) "
            "VALUES ('stationx', 'GOLD', 'demo', 'long', 0.01, 92170.0, 92170.0, 10.0, 2.0, '2026-08-20T00:00:00Z')"
        ).lastrowid


# --- compute_align_matinale (pure) -----------------------------------------

def test_align_matinale_long_aligned_with_bullish_bias():
    assert compute_align_matinale("long", "haussier") is True


def test_align_matinale_long_opposed_to_bearish_bias():
    assert compute_align_matinale("long", "baissier") is False


def test_align_matinale_short_aligned_with_bearish_bias():
    assert compute_align_matinale("short", "baissier") is True


def test_align_matinale_short_opposed_to_bullish_bias():
    assert compute_align_matinale("short", "haussier") is False


def test_align_matinale_none_when_biais_absent():
    assert compute_align_matinale("long", None) is None


def test_align_matinale_none_when_biais_neutre():
    assert compute_align_matinale("long", "neutre") is None


def test_align_matinale_none_when_biais_indetermine():
    assert compute_align_matinale("short", "indetermine") is None


def test_align_matinale_none_when_direction_unknown():
    assert compute_align_matinale("sideways", "haussier") is None


# --- get_latest_matinale_biais (I/O) ---------------------------------------

def test_get_latest_matinale_biais_returns_most_recent(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    _insert_matinale_summary(db_path, "GOLD", "baissier", "2026-08-19T07:00:00+00:00")
    _insert_matinale_summary(db_path, "GOLD", "haussier", "2026-08-20T07:00:00+00:00")

    biais = get_latest_matinale_biais(db_path, "GOLD", "2026-08-20T12:00:00+00:00")
    assert biais == "haussier"


def test_get_latest_matinale_biais_ignores_future_matinale(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    _insert_matinale_summary(db_path, "GOLD", "baissier", "2026-08-19T07:00:00+00:00")
    _insert_matinale_summary(db_path, "GOLD", "haussier", "2026-08-21T07:00:00+00:00")  # après le trade

    biais = get_latest_matinale_biais(db_path, "GOLD", "2026-08-20T12:00:00+00:00")
    assert biais == "baissier"


def test_get_latest_matinale_biais_none_when_no_matinale_on_asset(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    _insert_matinale_summary(db_path, "GOLD", "haussier", "2026-08-20T07:00:00+00:00")

    assert get_latest_matinale_biais(db_path, "EURUSD", "2026-08-20T12:00:00+00:00") is None


# --- record_align_matinale / record_align_matinale_for_trade (I/O) --------

def test_record_align_matinale_persists_true_as_one(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_dummy_trade(db_path)

    record_align_matinale(db_path, trade_id, True)

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT align_matinale FROM trade_features WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["align_matinale"] == 1
    finally:
        conn.close()


def test_record_align_matinale_persists_none_as_null(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_dummy_trade(db_path)

    record_align_matinale(db_path, trade_id, None)

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT align_matinale FROM trade_features WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["align_matinale"] is None
    finally:
        conn.close()


def test_record_align_matinale_for_trade_end_to_end(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    _insert_matinale_summary(db_path, "GOLD", "haussier", "2026-08-20T07:00:00+00:00")
    trade_id = _insert_dummy_trade(db_path)

    result = record_align_matinale_for_trade(db_path, trade_id, "GOLD", "long", "2026-08-20T12:00:00+00:00")

    assert result is True
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT align_matinale FROM trade_features WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["align_matinale"] == 1
    finally:
        conn.close()


def test_record_align_matinale_for_trade_no_matinale_available(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_dummy_trade(db_path)

    result = record_align_matinale_for_trade(db_path, trade_id, "EURUSD", "long", "2026-08-20T12:00:00+00:00")

    assert result is None
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT align_matinale FROM trade_features WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["align_matinale"] is None
    finally:
        conn.close()
