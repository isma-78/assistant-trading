"""
Tests de db — schéma SQLite (§4.5 du CDC v4 + ajouts documentés dans
docs/DECISIONS.md). Pas un module financier critique (pas d'exigence de
100% de couverture), mais une base cassée compromettrait tout le reste :
tests de fumée sur la création du schéma et le contexte transactionnel.
"""

import sqlite3

import pytest

from src.db import connection_scope, get_connection, init_db

EXPECTED_TABLES = {
    "raw_messages", "signals", "market_snapshots", "macro_events",
    "matinale_summaries", "suivi_events",
    "trades", "trade_partials", "trade_features",
    "envelopes", "envelope_ledger", "reserve_ledger",
    "confidence_scores", "allocations",
    "post_trade_review", "causal_analysis_log", "hypotheses",
    "metrics_snapshot", "rule_changes", "logs",
    "risk_decisions", "go_nogo_events", "trade_analysis",
}


def _table_names(conn: sqlite3.Connection) -> set:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def test_init_db_creates_all_expected_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        assert EXPECTED_TABLES.issubset(_table_names(conn))
    finally:
        conn.close()


def test_init_db_migrates_deal_id_onto_pre_existing_trades_table(tmp_path):
    # Reproduit le bug réel trouvé le 16/08/2026 : une base créée avant
    # l'ajout de trades.deal_id (palier P2) doit recevoir la colonne au
    # prochain init_db(), pas rester bloquée dessus indéfiniment
    # (CREATE TABLE IF NOT EXISTS ne migre jamais une table existante).
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, actif TEXT NOT NULL, statut TEXT)"
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "deal_id" in columns
    finally:
        conn.close()


def test_init_db_migrates_envelopes_source_preserving_data_and_ids(tmp_path):
    # Reproduit la situation réelle du VPS le 20/08/2026 : une base créée
    # avant l'ajout de envelopes.source (Flux B) contient déjà une
    # enveloppe avec un solde réel et un id référencé par envelope_ledger
    # — la migration doit préserver les deux, pas juste ajouter la colonne
    # (impossible ici : la contrainte UNIQUE doit aussi changer).
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE envelopes (id INTEGER PRIMARY KEY AUTOINCREMENT, actif TEXT NOT NULL, "
        "mode TEXT NOT NULL, capital_initial REAL NOT NULL, capital_courant REAL NOT NULL, "
        "nb_rechargements INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "UNIQUE (actif, mode))"
    )
    conn.execute(
        "INSERT INTO envelopes (id, actif, mode, capital_initial, capital_courant, nb_rechargements, "
        "created_at, updated_at) VALUES (7, 'BTCUSD', 'demo', 500.0, 499.66, 0, 't0', 't1')"
    )
    conn.execute(
        "CREATE TABLE envelope_ledger (id INTEGER PRIMARY KEY, "
        "envelope_id INTEGER NOT NULL REFERENCES envelopes(id))"
    )
    conn.execute("INSERT INTO envelope_ledger (id, envelope_id) VALUES (1, 7)")
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(envelopes)")}
        assert "source" in columns

        row = conn.execute("SELECT * FROM envelopes WHERE id = 7").fetchone()
        assert row["actif"] == "BTCUSD"
        assert row["capital_courant"] == 499.66
        assert row["source"] == "stationx"

        # L'id est préservé -> la référence de envelope_ledger reste valide
        ledger_row = conn.execute("SELECT * FROM envelope_ledger WHERE envelope_id = 7").fetchone()
        assert ledger_row is not None
    finally:
        conn.close()


def test_init_db_envelopes_allows_same_actif_mode_different_source(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO envelopes (actif, mode, source, capital_initial, capital_courant, created_at, updated_at) "
            "VALUES ('EURUSD', 'demo', 'stationx', 500.0, 500.0, 't0', 't0')"
        )
        # Ne doit PAS lever : même (actif, mode), source différente
        conn.execute(
            "INSERT INTO envelopes (actif, mode, source, capital_initial, capital_courant, created_at, updated_at) "
            "VALUES ('EURUSD', 'demo', 'hypothesis', 500.0, 500.0, 't0', 't0')"
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM envelopes WHERE actif = 'EURUSD' AND mode = 'demo'"
        ).fetchone()["n"]
        assert count == 2
    finally:
        conn.close()


def test_init_db_migrates_trade_analysis_source_column(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trade_analysis)")}
        assert "source" in columns
    finally:
        conn.close()


def test_init_db_migrates_signals_take_profit_column(tmp_path):
    # signals.take_profit (Hypothèse #4, retour à la moyenne, voir
    # docs/HYPOTHESES.md) : une base créée avant son ajout doit la
    # recevoir au prochain init_db(), même patron que deal_id ci-dessus.
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, actif TEXT, statut TEXT)"
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(signals)")}
        assert "take_profit" in columns
    finally:
        conn.close()


def test_init_db_migrates_regime_type_column(tmp_path):
    # trades.regime_type (bascule du régime de l'Hypothèse #2, 23/08/2026
    # — voir docs/DECISIONS.md) : une base créée avant son ajout doit la
    # recevoir au prochain init_db(), même patron que deal_id ci-dessus.
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, actif TEXT, source TEXT, statut TEXT)"
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "regime_type" in columns
    finally:
        conn.close()


def test_init_db_backfills_regime_type_ma200_for_pre_bascule_hypothesis_trades(tmp_path):
    # Les trades H1/H2/H3/H4 déjà en base avant l'ajout de la colonne
    # doivent être rétro-remplis "ma200" (aucun n'a jamais tourné sous
    # le régime structurel avant cette date, y compris H2 — sa bascule
    # et cette migration sont déployées dans le même changement, voir
    # docs/DECISIONS.md). Station X (source arbitraire, canal Telegram)
    # reste NULL : aucune notion de régime de fond.
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, actif TEXT, source TEXT, statut TEXT)"
    )
    for source in ("hypothesis", "hypothesis2", "hypothesis3", "hypothesis4", "-1002481537588"):
        conn.execute("INSERT INTO trades (actif, source, statut) VALUES ('EURUSD', ?, 'ferme')", (source,))
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = {row["source"]: row["regime_type"] for row in conn.execute("SELECT source, regime_type FROM trades")}
    finally:
        conn.close()
    assert rows["hypothesis"] == "ma200"
    assert rows["hypothesis2"] == "ma200"  # trade pré-bascule, jamais "structural_bos_choch"
    assert rows["hypothesis3"] == "ma200"
    assert rows["hypothesis4"] == "ma200"
    assert rows["-1002481537588"] is None  # Station X : pas de notion de régime


def test_init_db_backfill_regime_type_is_idempotent_and_never_touches_future_trades(tmp_path):
    # Un trade H2 ouvert APRÈS la bascule (regime_type déjà renseigné à
    # l'écriture, jamais NULL) ne doit JAMAIS être réécrit par le
    # backfill, même après plusieurs appels à init_db().
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, stop_loss_initial, "
            "stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut, regime_type) "
            "VALUES ('d1', 'hypothesis2', 'EURUSD', 'demo', 'long', 100, 1.0, 1.0, 10.0, 2.0, "
            "'2026-08-23T00:00:00Z', 'ouvert', 'structural_bos_choch')"
        )

    init_db(db_path)  # second appel : idempotence

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT regime_type FROM trades WHERE deal_id = 'd1'").fetchone()
    finally:
        conn.close()
    assert row["regime_type"] == "structural_bos_choch"


def test_init_db_backfill_regime_type_skips_table_without_source_column(tmp_path):
    # Garde-fou : une table trades encore plus dépouillée que celle visée
    # par la migration deal_id (sans même `source`) ne doit jamais faire
    # échouer init_db() — rien à rétro-remplir dans ce cas (voir
    # docstring de db._backfill_regime_type).
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, actif TEXT NOT NULL, statut TEXT)"
    )
    conn.commit()
    conn.close()

    init_db(db_path)  # ne doit lever aucune exception

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "regime_type" in columns
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    init_db(db_path)  # ne doit pas lever
    conn = get_connection(db_path)
    try:
        assert EXPECTED_TABLES.issubset(_table_names(conn))
    finally:
        conn.close()


def test_get_connection_creates_parent_directory(tmp_path):
    db_path = str(tmp_path / "nested" / "dir" / "test.db")
    conn = get_connection(db_path)
    conn.close()
    assert (tmp_path / "nested" / "dir" / "test.db").exists()


def test_get_connection_enables_foreign_keys(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    try:
        fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_status == 1
    finally:
        conn.close()


def test_connection_scope_commits_on_success(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO raw_messages "
            "(telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, NULL, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'autre')"
        )

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM raw_messages").fetchone()
        assert row["n"] == 1
    finally:
        conn.close()


def test_connection_scope_rolls_back_on_exception(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        with connection_scope(db_path) as conn:
            conn.execute(
                "INSERT INTO raw_messages "
                "(telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type) "
                "VALUES (1, NULL, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'autre')"
            )
            # Violation de la contrainte UNIQUE(channel, telegram_msg_id) : provoque le rollback
            conn.execute(
                "INSERT INTO raw_messages "
                "(telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type) "
                "VALUES (1, NULL, 'station_x', '2026-08-16T00:00:01Z', 'texte2', 'autre')"
            )

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM raw_messages").fetchone()
        assert row["n"] == 0
    finally:
        conn.close()


def test_raw_messages_unique_constraint_prevents_duplicate_capture(tmp_path):
    # Un même message Telegram (même canal, même id) ne doit jamais être
    # capturé deux fois — protège telegram_listener contre un double
    # traitement en cas de redémarrage/reprise.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO raw_messages "
            "(telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, NULL, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'autre')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO raw_messages "
                "(telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type) "
                "VALUES (1, NULL, 'station_x', '2026-08-16T00:00:01Z', 'texte2', 'autre')"
            )
    finally:
        conn.close()
