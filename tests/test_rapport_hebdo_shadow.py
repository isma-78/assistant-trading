"""
tests/test_rapport_hebdo_shadow.py — Script du rapport hebdomadaire
shadow (07/10/2026, étape 4). Vérifie les fonctions de lecture DB et
l'écriture du rapport sur une base temporaire réelle.
"""

import importlib.util
import os
import sqlite3

from src.db import init_db

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "rapport_hebdo_shadow.py")
_spec = importlib.util.spec_from_file_location("rapport_hebdo_shadow", _PATH)
rhs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rhs)


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_load_shadow_for_report_returns_none_before_epoch(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    started_at, refs, raw = rhs.load_shadow_for_report(conn, "hypothesis_v3cand")
    assert started_at is None and refs == [] and raw == []


def test_load_shadow_for_report_filters_closed_since_epoch(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    conn.execute("INSERT INTO shadow_epochs (source, started_at, description) VALUES "
                 "('hypothesis_v3cand', '2026-10-10T00:00:00', 'T0')")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, ferme_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v3cand', 'GOLD', 'long', 100, 90, 90, '2026-10-11T00:00:00', "
                 "'2026-10-12T00:00:00', 'ferme', 1.0)")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v3cand', 'GOLD', 'long', 100, 90, 90, '2026-10-13T00:00:00', 'ouvert', NULL)")
    conn.commit()
    started_at, refs, raw = rhs.load_shadow_for_report(conn, "hypothesis_v3cand")
    assert started_at == "2026-10-10T00:00:00" and len(refs) == 1 and refs[0].r_multiple == 1.0
    assert len(raw) == 1 and raw[0]["ferme_at"] == "2026-10-12T00:00:00"


def test_load_live_baseline_excludes_anomalies(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    conn.execute("INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
                 "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, "
                 "statut, r_multiple_total) VALUES ('hypothesis_v2','GOLD','demo','long',1,100,90,90,10,2,"
                 "'2026-10-11T00:00:00','ferme',1.0)")
    conn.commit()
    refs = rhs.load_live_baseline(conn, "hypothesis_v2", "2026-10-10T00:00:00")
    assert len(refs) == 1 and refs[0].r_multiple == 1.0


def test_script_importable_from_repo_root_like_cron():
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"import runpy; runpy.run_path({os.path.join(root, 'scripts', 'rapport_hebdo_shadow.py')!r}, run_name='not_main')"
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
