"""
tests/test_shadow_milestone_alerts.py — Script d'orchestration des
alertes/verdicts shadow (06/10/2026, voir
docs/PROTOCOLE_EVOLUTION_V2_06-10.md §6-7). Vérifie les fonctions de
lecture DB (pures sur une base réelle temporaire) et l'idempotence du
marquage des alertes déjà envoyées.
"""

import importlib.util
import json
import os
import sqlite3

from src.db import init_db

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "shadow_milestone_alerts.py")
_spec = importlib.util.spec_from_file_location("shadow_milestone_alerts", _PATH)
sma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sma)


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_load_shadow_closed_returns_none_before_any_epoch(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    assert sma.load_shadow_closed(conn, "hypothesis_v3cand") == (None, [])


def test_load_shadow_closed_filters_by_epoch_start_and_status(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    conn.execute("INSERT INTO shadow_epochs (source, started_at, description) VALUES "
                 "('hypothesis_v3cand', '2026-10-10T00:00:00', 'T0')")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v3cand', 'GOLD', 'long', 100, 90, 90, '2026-10-11T00:00:00', 'ferme', 1.0)")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v3cand', 'GOLD', 'long', 100, 90, 90, '2026-10-12T00:00:00', 'ouvert', NULL)")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v3cand', 'GOLD', 'long', 100, 90, 90, '2026-09-01T00:00:00', 'ferme', 5.0)")  # avant T0
    conn.commit()
    started_at, trades = sma.load_shadow_closed(conn, "hypothesis_v3cand")
    assert started_at == "2026-10-10T00:00:00"
    assert len(trades) == 1 and trades[0].r_multiple == 1.0


def test_load_live_baseline_excludes_anomalies_and_unpriced(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    conn.execute("INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
                 "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, "
                 "statut, r_multiple_total) VALUES ('hypothesis_v2','GOLD','demo','long',1,100,90,90,10,2,"
                 "'2026-10-11T00:00:00','ferme',1.0)")
    conn.execute("INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
                 "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, "
                 "statut, r_multiple_total, anomalie_technique) VALUES ('hypothesis_v2','GOLD','demo','long',1,100,90,90,10,2,"
                 "'2026-10-11T00:00:00','ferme',2.0,'tp1_cloture_totale_broker')")
    conn.commit()
    trades = sma.load_live_baseline(conn, "hypothesis_v2", "2026-10-10T00:00:00")
    assert len(trades) == 1 and trades[0].r_multiple == 1.0


def test_already_alerted_and_mark_alerted_round_trip(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    assert sma.already_alerted(conn, "hypothesis_v3cand") == []
    sma.mark_alerted(conn, "hypothesis_v3cand", [30], "2026-12-01T00:00:00")
    conn.commit()
    assert sma.already_alerted(conn, "hypothesis_v3cand") == [30]
    sma.mark_alerted(conn, "hypothesis_v3cand", [30, 40], "2026-12-15T00:00:00")
    conn.commit()
    assert sma.already_alerted(conn, "hypothesis_v3cand") == [30, 40]


def test_script_importable_from_repo_root_like_cron():
    """Même garde-fou que test_financing_capture.py : le cron lance
    `venv/bin/python scripts/shadow_milestone_alerts.py` depuis la racine,
    seul `scripts/` est dans sys.path à ce moment."""
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for script in ("shadow_milestone_alerts.py", "run_shadow_cycle.py"):
        code = f"import runpy; runpy.run_path({os.path.join(root, 'scripts', script)!r}, run_name='not_main')"
        result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
