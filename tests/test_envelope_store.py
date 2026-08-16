"""
Tests d'envelope_store — persistance DB des enveloppes et de la réserve
globale (§2.3 du CDC). Utilise une base SQLite temporaire réelle (pas de
mock) : c'est un test d'intégration léger sur le schéma db.py.
"""

from src.capital_manager import CapitalManager, apply_trade_result
from src.db import connection_scope, get_connection, init_db
from src.envelope_store import load_or_create_envelope, load_reserve_total, persist_trade_result


def _insert_dummy_trade(db_path: str) -> int:
    """envelope_ledger/trades ont une FK réelle (PRAGMA foreign_keys=ON) —
    un trade_id passé à persist_trade_result doit correspondre à une
    ligne trades existante, comme en usage réel (le trade vient d'être
    clos avant l'appel)."""
    with connection_scope(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at) "
            "VALUES ('station_x', 'GOLD', 'demo', 'short', 0.01, 92170.0, 92170.0, 10.0, 2.0, '2026-08-16T00:00:00Z')"
        )
        return cursor.lastrowid


def test_load_or_create_envelope_creates_new_with_initial_capital(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    envelope_id, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)

    assert envelope_id is not None
    assert manager.balance == 500.0

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM envelopes WHERE id = ?", (envelope_id,)).fetchone()
        assert row["actif"] == "GOLD"
        assert row["mode"] == "demo"
        assert row["capital_initial"] == 500.0
        assert row["capital_courant"] == 500.0

        ledger_row = conn.execute(
            "SELECT * FROM envelope_ledger WHERE envelope_id = ?", (envelope_id,)
        ).fetchone()
        assert ledger_row["type_mouvement"] == "init"
        assert ledger_row["montant_apres"] == 500.0
    finally:
        conn.close()


def test_load_or_create_envelope_loads_existing_balance(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    envelope_id_1, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)
    persist_trade_result(db_path, envelope_id_1, manager, trade_id=None, balance_before=500.0, reserve_share=0.0, reserve_total_after=0.0)
    manager.apply_trade_pnl(25.0, "gain test")
    trade_id = _insert_dummy_trade(db_path)
    persist_trade_result(db_path, envelope_id_1, manager, trade_id=trade_id, balance_before=500.0, reserve_share=0.0, reserve_total_after=0.0)

    envelope_id_2, reloaded = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)

    assert envelope_id_2 == envelope_id_1
    assert reloaded.balance == 525.0


def test_load_or_create_envelope_separate_per_asset_and_mode(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    gold_id, gold_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)
    btc_id, btc_manager = load_or_create_envelope(db_path, "BTCUSD", "demo", 500.0)

    assert gold_id != btc_id
    gold_manager.apply_trade_pnl(-50.0, "perte")
    trade_id = _insert_dummy_trade(db_path)
    persist_trade_result(db_path, gold_id, gold_manager, trade_id=trade_id, balance_before=500.0, reserve_share=0.0, reserve_total_after=0.0)

    _, btc_reloaded = load_or_create_envelope(db_path, "BTCUSD", "demo", 500.0)
    assert btc_reloaded.balance == 500.0  # pas affecté par le mouvement sur GOLD


def test_load_reserve_total_zero_when_no_movements(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert load_reserve_total(db_path) == 0.0


def test_persist_trade_result_full_cycle_winning_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    envelope_id, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)
    reserve_before = load_reserve_total(db_path)
    balance_before = manager.balance

    trade_id = _insert_dummy_trade(db_path)
    reserve_share, reserve_total = apply_trade_result(manager, pnl=40.0, reserve_total_before=reserve_before, note="TP1 touché")
    persist_trade_result(db_path, envelope_id, manager, trade_id=trade_id, balance_before=balance_before, reserve_share=reserve_share, reserve_total_after=reserve_total)

    assert manager.balance == 520.0  # 500 + 50% de 40
    assert load_reserve_total(db_path) == 20.0

    conn = get_connection(db_path)
    try:
        reserve_row = conn.execute("SELECT * FROM reserve_ledger WHERE trade_id = ?", (trade_id,)).fetchone()
        assert reserve_row["montant_ajoute"] == 20.0
        assert reserve_row["reserve_totale"] == 20.0
    finally:
        conn.close()


def test_persist_trade_result_losing_trade_no_reserve_row(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    envelope_id, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)
    reserve_before = load_reserve_total(db_path)
    balance_before = manager.balance

    trade_id = _insert_dummy_trade(db_path)
    reserve_share, reserve_total = apply_trade_result(manager, pnl=-15.0, reserve_total_before=reserve_before, note="SL touché")
    persist_trade_result(db_path, envelope_id, manager, trade_id=trade_id, balance_before=balance_before, reserve_share=reserve_share, reserve_total_after=reserve_total)

    assert manager.balance == 485.0
    assert load_reserve_total(db_path) == 0.0

    conn = get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM reserve_ledger").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()
