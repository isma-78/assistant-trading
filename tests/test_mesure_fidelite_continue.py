"""
tests/test_mesure_fidelite_continue.py — Script de remesure mensuelle de
fidélité (07/10/2026, étape 4). Vérifie le chargement DB et
l'importabilité façon cron ; la logique de décision elle-même est déjà
testée dans tests/test_fidelity_continuous.py (réutilisée, pas
dupliquée ici).
"""

import importlib.util
import os
import sqlite3

from src.db import init_db

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "mesure_fidelite_continue.py")
_spec = importlib.util.spec_from_file_location("mesure_fidelite_continue", _PATH)
mfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mfc)


def test_load_live_filters_by_cutoff_and_status(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
                 "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, "
                 "statut, r_multiple_total) VALUES ('hypothesis_v2','GOLD','demo','long',1,100,90,90,10,2,"
                 "'2026-09-01T00:00:00','ferme',1.0)")
    conn.execute("INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
                 "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, "
                 "statut, r_multiple_total) VALUES ('hypothesis_v2','GOLD','demo','long',1,100,90,90,10,2,"
                 "'2026-10-01T00:00:00','ferme',1.0)")  # hors de portée (>= cutoff)
    conn.commit()
    refs = mfc.load_live(conn, "hypothesis_v2", "2026-09-25T18:00:00")
    assert len(refs) == 1 and refs[0].entry_time_utc == "2026-09-01T00:00:00"


def test_load_backtest_replayer_exposes_expected_symbols():
    replayer = mfc.load_backtest_replayer()
    assert set(replayer.HYP) == {"H1", "H2", "H3", "H4"}
    assert hasattr(replayer, "replay_period") and hasattr(replayer, "DATA_CUTOFF") and hasattr(replayer, "ASSETS")


def test_script_importable_from_repo_root_like_cron():
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"import runpy; runpy.run_path({os.path.join(root, 'scripts', 'mesure_fidelite_continue.py')!r}, run_name='not_main')"
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
