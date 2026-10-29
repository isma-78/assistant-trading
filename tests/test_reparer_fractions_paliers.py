import importlib.util
import pathlib

from src.db import connection_scope, get_connection, init_db

_spec = importlib.util.spec_from_file_location(
    "reparer", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "reparer_fractions_paliers.py")
reparer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reparer)


def _trade(db_path, legs, partials, taille=0.277):
    with connection_scope(db_path) as conn:
        tid = conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('d', 'hypothesis_v2', 'ETHUSD', 'demo', 'short', ?, 100, 101, 101, 10.0, 2.0, '2026-10-14T00:00:00Z', 'ouvert')",
            (taille,),
        ).lastrowid
        for palier, size, statut in legs:
            conn.execute("INSERT INTO trade_legs (trade_id, palier, taille, order_deal_id, statut) VALUES (?, ?, ?, 'o', ?)",
                         (tid, palier, size, statut))
        for palier, fraction in partials:
            conn.execute("INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at) "
                         "VALUES (?, ?, ?, 99, 1.0, 'x', '2026-10-15T00:00:00Z')", (tid, palier, fraction))
    return tid


def test_repairs_case_of_trade_17028_and_is_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    tid = _trade(db, [("tp1", 0.138, "annule"), ("tp2", 0.083, "annule"), ("runner", 0.056, "ferme")],
                 [("tp1", 0.0), ("tp2", 0.0), ("sl", 0.056 / 0.277)])
    assert reparer.repair(db, apply=False) == [(tid, "reparable", 0.056 / 0.277)]
    conn = get_connection(db)
    assert conn.execute("SELECT taille_initiale FROM trades").fetchone()[0] == 0.277  # simulation : rien modifié
    conn.close()
    reparer.repair(db, apply=True)
    conn = get_connection(db)
    row = conn.execute("SELECT taille_initiale, risque_eur FROM trades").fetchone()
    assert abs(row[0] - 0.056) < 1e-12 and abs(row[1] - 10.0 * 0.056 / 0.277) < 1e-9
    assert abs(conn.execute("SELECT SUM(fraction) FROM trade_partials").fetchone()[0] - 1.0) < 1e-9
    conn.close()
    assert reparer.repair(db, apply=True) == []


def test_unrepairable_trade_is_marked_not_usable_not_modified(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    tid = _trade(db, [("tp1", 0.138, "ferme"), ("runner", 0.056, "ferme")], [("sl", 0.3)])
    assert reparer.repair(db, apply=True) == [(tid, "non_reparable", 0.3)]
    conn = get_connection(db)
    assert conn.execute("SELECT anomalie_technique FROM trades").fetchone()[0] == "fractions_paliers_incoherentes"
    assert conn.execute("SELECT taille_initiale FROM trades").fetchone()[0] == 0.277
    conn.close()


def test_consistent_trade_untouched(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    _trade(db, [("runner", 0.277, "ferme")], [("sl", 1.0)])
    assert reparer.repair(db, apply=True) == []
