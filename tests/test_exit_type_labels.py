"""A6 (bilan du 05/10/2026) : étiquette exit_type des sources _v2."""

import importlib.util
import os
import sqlite3

from src.db import init_db
from src.executor import _EXIT_TYPE_BY_SOURCE

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "fix_exit_type_labels.py")
_spec = importlib.util.spec_from_file_location("fix_exit_type_labels", _PATH)
fix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix)


def test_v2_sources_have_explicit_exit_labels():
    assert _EXIT_TYPE_BY_SOURCE["hypothesis5_v2"] == "trailing_pur"
    for source in ("hypothesis_v2", "hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2"):
        assert _EXIT_TYPE_BY_SOURCE[source] == "tp_partiel"


def _insert(conn, source, exit_type):
    conn.execute(
        "INSERT INTO trades (source, actif, mode, direction, taille_initiale, prix_entree_prevu, stop_loss_initial, "
        "stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut, exit_type, r_multiple_total) "
        "VALUES (?, 'GOLD', 'demo', 'long', 1, 100, 99, 99, 10, 2, '2026-10-01', 'ferme', ?, -1.0)",
        (source, exit_type),
    )


def test_fix_labels_dry_run_then_apply_only_touches_h5_label(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    _insert(conn, "hypothesis5_v2", "tp_partiel")
    _insert(conn, "hypothesis5_v2", "tp_partiel")
    _insert(conn, "hypothesis2_v2", "tp_partiel")
    conn.commit()
    conn.close()

    assert fix.fix_labels(db_path, apply=False) == 2
    assert fix.fix_labels(db_path, apply=True) == 2
    assert fix.fix_labels(db_path, apply=True) == 0

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT source, exit_type, r_multiple_total FROM trades ORDER BY id").fetchall()
    conn.close()
    assert rows == [("hypothesis5_v2", "trailing_pur", -1.0), ("hypothesis5_v2", "trailing_pur", -1.0),
                    ("hypothesis2_v2", "tp_partiel", -1.0)]
