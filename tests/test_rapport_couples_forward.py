"""Tests de scripts/rapport_couples_forward.py (Partie 4, 08/10/2026)."""

import importlib.util
import os
import sqlite3

from src.db import init_db

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "rapport_couples_forward.py")
_spec = importlib.util.spec_from_file_location("rapport_couples_forward", _PATH)
rcf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcf)


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_baseline_source_strips_suffix():
    assert rcf.BASELINE_SOURCE["hypothesis_v2_shadow_couples"] == "hypothesis_v2"
    assert rcf.BASELINE_SOURCE["hypothesis2_v2_shadow_couples"] == "hypothesis2_v2"
    assert len(rcf.BASELINE_SOURCE) == 5


def test_load_shadow_couple_empty(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    refs, raw = rcf.load_shadow_couple(conn, "hypothesis_v2_shadow_couples", "GOLD")
    assert refs == [] and raw == []


def test_load_shadow_couple_filters_closed_only(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, ferme_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v2_shadow_couples', 'GOLD', 'long', 100, 90, 90, '2026-10-11T00:00:00', "
                 "'2026-10-12T00:00:00', 'ferme', 0.5)")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v2_shadow_couples', 'GOLD', 'long', 100, 90, 90, '2026-10-13T00:00:00', "
                 "'ouvert', NULL)")
    conn.commit()
    refs, raw = rcf.load_shadow_couple(conn, "hypothesis_v2_shadow_couples", "GOLD")
    assert len(refs) == 1 and refs[0].r_multiple == 0.5
    assert len(raw) == 1


def test_load_live_couple_excludes_anomalies_and_unreconciled(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    common = "mode, taille_initiale, stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique"
    common_vals = "'demo', 1.0, 90, 90, 10.0, 2.0"
    conn.execute(f"INSERT INTO trades (source, actif, direction, ouvert_at, statut, r_multiple_total, "
                 f"anomalie_technique, {common}) VALUES ('hypothesis_v2', 'GOLD', 'long', "
                 f"'2026-09-01T00:00:00', 'ferme', 0.3, NULL, {common_vals})")
    conn.execute(f"INSERT INTO trades (source, actif, direction, ouvert_at, statut, r_multiple_total, "
                 f"anomalie_technique, {common}) VALUES ('hypothesis_v2', 'GOLD', 'long', "
                 f"'2026-09-02T00:00:00', 'ferme', 0.9, 'tp1_cloture_totale_broker', {common_vals})")
    conn.execute(f"INSERT INTO trades (source, actif, direction, ouvert_at, statut, r_multiple_total, {common}) "
                 f"VALUES ('hypothesis_v2', 'GOLD', 'long', '2026-09-03T00:00:00', 'ferme_non_reconcilie', "
                 f"NULL, {common_vals})")
    conn.commit()
    refs = rcf.load_live_couple(conn, "hypothesis_v2", "GOLD")
    assert len(refs) == 1 and refs[0].r_multiple == 0.3


def test_main_writes_report_file(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = _conn(db_path)
    conn.execute("INSERT INTO trades (source, actif, direction, ouvert_at, statut, r_multiple_total, "
                 "mode, taille_initiale, stop_loss_initial, stop_loss_courant, risque_eur, "
                 "pourcentage_risque_applique) VALUES ('hypothesis_v2', 'GOLD', 'long', "
                 "'2026-09-01T00:00:00', 'ferme', 0.4, 'demo', 1.0, 90, 90, 10.0, 2.0)")
    conn.execute("INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
                 "stop_loss_courant, ouvert_at, ferme_at, statut, r_multiple_total) VALUES "
                 "('hypothesis_v2_shadow_couples', 'GOLD', 'long', 100, 90, 90, '2026-09-05T00:00:00', "
                 "'2026-09-06T00:00:00', 'ferme', 0.2)")
    conn.commit()
    conn.close()

    class _FakeConfig:
        def __init__(self):
            self.db_path = db_path

    monkeypatch.setattr("src.config.load_config", lambda: _FakeConfig())
    report_dir = tmp_path / "SUIVI_COUPLES"
    monkeypatch.setattr(rcf, "REPORT_DIR", str(report_dir))

    code = rcf.main()
    assert code == 0
    files = list(report_dir.iterdir())
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "hypothesis_v2 x GOLD" in content
