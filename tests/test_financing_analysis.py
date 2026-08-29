"""Tests de financing_analysis.py (point 7, 29/08/2026, voir docs/DECISIONS.md)."""

from src.db import connection_scope, init_db
from src.financing_analysis import (
    aggregate_financing_by_asset_direction,
    aggregate_financing_rows,
    resolve_direction_at_time,
)


def _insert_trade(conn, actif, direction, ouvert_at, ferme_at=None, statut="ferme", source="hypothesis5"):
    conn.execute(
        "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
        "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
        "pourcentage_risque_applique, ouvert_at, ferme_at, statut) "
        "VALUES (NULL, ?, ?, 'demo', ?, 0.01, 100.0, 100.0, 99.0, 99.0, 10.0, 2.0, ?, ?, ?)",
        (source, actif, direction, ouvert_at, ferme_at, statut),
    )


# ---------------------------------------------------------------------------
# resolve_direction_at_time
# ---------------------------------------------------------------------------

def test_resolve_direction_finds_single_open_trade_spanning_timestamp(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "USDJPY", "long", "2026-08-27T00:00:00Z", "2026-08-29T00:00:00Z")
    assert resolve_direction_at_time(db_path, "USDJPY", "2026-08-28T21:02:07.525") == "long"


def test_resolve_direction_still_open_trade_no_ferme_at(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "GOLD", "short", "2026-08-27T00:00:00Z", ferme_at=None, statut="ouvert")
    assert resolve_direction_at_time(db_path, "GOLD", "2026-08-28T21:01:24.811") == "short"


def test_resolve_direction_none_when_no_trade_spans_timestamp(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "USDJPY", "long", "2026-08-29T00:00:00Z", "2026-08-30T00:00:00Z")
    # Le financement a ete debite AVANT l'ouverture du trade connu.
    assert resolve_direction_at_time(db_path, "USDJPY", "2026-08-28T21:02:07.525") is None


def test_resolve_direction_none_when_trade_already_closed_before_timestamp(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "USDJPY", "long", "2026-08-20T00:00:00Z", "2026-08-21T00:00:00Z")
    # Le trade s'est ferme bien avant le debit de financement.
    assert resolve_direction_at_time(db_path, "USDJPY", "2026-08-28T21:02:07.525") is None


def test_resolve_direction_none_when_multiple_conflicting_directions_open(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "GBPUSD", "long", "2026-08-27T00:00:00Z", "2026-08-29T00:00:00Z", source="hypothesis")
        _insert_trade(conn, "GBPUSD", "short", "2026-08-27T00:00:00Z", "2026-08-29T00:00:00Z", source="hypothesis3")
    assert resolve_direction_at_time(db_path, "GBPUSD", "2026-08-28T21:01:26.167") is None


def test_resolve_direction_accepts_mixed_iso_formats(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "ETHUSD", "long", "2026-08-16T19:03:27.857413+00:00", None, statut="ouvert")
    assert resolve_direction_at_time(db_path, "ETHUSD", "2026-08-28T21:01:06.293") == "long"


# ---------------------------------------------------------------------------
# aggregate_financing_rows (pur)
# ---------------------------------------------------------------------------

def test_aggregate_financing_rows_groups_by_asset_and_direction():
    rows = [
        {"instrument": "USDJPY", "size_eur": 0.23, "direction": "long"},
        {"instrument": "USDJPY", "size_eur": 0.19, "direction": "long"},
        {"instrument": "GBPUSD", "size_eur": -0.25, "direction": "short"},
    ]
    result = aggregate_financing_rows(rows)
    assert {"actif": "USDJPY", "direction": "long", "n": 2, "financing_moyen_eur": 0.21} in [
        {**r, "financing_moyen_eur": round(r["financing_moyen_eur"], 2)} for r in result
    ]
    assert {"actif": "GBPUSD", "direction": "short", "n": 1, "financing_moyen_eur": -0.25} in result


def test_aggregate_financing_rows_groups_unresolved_direction_separately():
    rows = [{"instrument": "GOLD", "size_eur": -0.02, "direction": None}]
    result = aggregate_financing_rows(rows)
    assert result == [{"actif": "GOLD", "direction": "indetermine", "n": 1, "financing_moyen_eur": -0.02}]


def test_aggregate_financing_rows_empty_input_returns_empty_list():
    assert aggregate_financing_rows([]) == []


# ---------------------------------------------------------------------------
# aggregate_financing_by_asset_direction (orchestration)
# ---------------------------------------------------------------------------

def test_aggregate_financing_by_asset_direction_end_to_end(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "USDJPY", "long", "2026-08-27T00:00:00Z", "2026-08-29T00:00:00Z")
        conn.execute(
            "INSERT INTO financing_transactions (reference, instrument, size_eur, date_utc, captured_at) "
            "VALUES ('ref1', 'USDJPY', 0.23, '2026-08-28T21:02:07.525', '2026-08-29T00:00:00Z')"
        )

    result = aggregate_financing_by_asset_direction(db_path)
    assert result == [{"actif": "USDJPY", "direction": "long", "n": 1, "financing_moyen_eur": 0.23}]


def test_aggregate_financing_by_asset_direction_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert aggregate_financing_by_asset_direction(db_path) == []
