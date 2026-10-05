"""verdict_counter (bilan du 05/10/2026, décisions 3/4/7, A9)."""

import sqlite3

import pytest

from src.db import init_db
from src.verdict_counter import count_all, counts_toward_verdict, reached_milestones


def _trade(conn, source, actif, statut="ferme", r=1.0, ouvert="2026-10-01T00:00:00", anomalie=None):
    return conn.execute(
        "INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, stop_loss_initial, "
        "stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut, r_multiple_total, anomalie_technique) "
        "VALUES (?, ?, 'demo', 'long', 1, 100, 99, 99, 10, 2, ?, ?, ?, ?)",
        (source, actif, ouvert, statut, r, anomalie),
    ).lastrowid


def _partial(conn, trade_id, fraction, real):
    conn.execute(
        "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at, prix_sortie_reel) "
        "VALUES (?, 'tp1', ?, 101, 1, 'x', '2026-10-02', ?)",
        (trade_id, fraction, real),
    )


def test_chfjpy_excluded_only_for_h2_to_h5():
    assert counts_toward_verdict("hypothesis_v2", "CHFJPY")
    for source in ("hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2", "hypothesis5_v2"):
        assert not counts_toward_verdict(source, "CHFJPY")
        assert counts_toward_verdict(source, "USDJPY")


def test_reached_milestones():
    assert reached_milestones(29, 30) == [30]
    assert reached_milestones(30, 30) == []
    assert reached_milestones(10, 60) == [30, 40, 53]


def test_count_all_applies_epoch_anomaly_chfjpy_and_broker_price_rules(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO hypothesis_epochs (source, epoch, started_at, description) VALUES "
                 "('hypothesis2_v2', 'E1', '2026-09-25T19:12:26Z', 'x')")
    _trade(conn, "hypothesis2_v2", "EURUSD", r=0.5)                          # compté
    priced = _trade(conn, "hypothesis2_v2", "GOLD", r=-1.0)                  # compté (prix broker présent)
    _partial(conn, priced, 1.0, 2000.0)
    unpriced = _trade(conn, "hypothesis2_v2", "US100", r=1.2)                # fermé sans prix broker -> à part
    _partial(conn, unpriced, 0.5, None)
    skipped = _trade(conn, "hypothesis2_v2", "US30", r=0.0)                  # palier non rempli (fraction 0) : ok
    _partial(conn, skipped, 0.0, None)
    _trade(conn, "hypothesis2_v2", "CHFJPY", r=1.0)                          # A9 : exclu
    _trade(conn, "hypothesis2_v2", "EURUSD", r=1.0, ouvert="2026-09-20T00:00:00")  # époque antérieure
    _trade(conn, "hypothesis2_v2", "EURUSD", r=1.0, anomalie="tp1_cloture_totale_broker")
    _trade(conn, "hypothesis2_v2", "EURUSD", statut="ouvert", r=None)
    _trade(conn, "hypothesis2_v2", "EURUSD", statut="ferme_non_reconcilie", r=None)
    _trade(conn, "hypothesis_v2", "CHFJPY", r=1.0)                           # H1 : CHFJPY compte
    conn.commit()
    conn.close()

    counts = {c.source: c for c in count_all(db_path)}
    h2 = counts["hypothesis2_v2"]
    assert h2.epoch == "E1"
    assert sorted(h2.reconciled_r) == [-1.0, 0.0, 0.5]
    assert h2.mean_r == pytest.approx(-1.0 / 6)
    assert (h2.closed_without_broker_price, h2.open_trades, h2.ghosts, h2.chfjpy_excluded) == (1, 1, 1, 1)
    assert counts["hypothesis_v2"].n == 1
    assert counts["hypothesis5_v2"].n == 0 and counts["hypothesis5_v2"].mean_r is None
