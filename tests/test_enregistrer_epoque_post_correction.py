import importlib.util
import sqlite3
from pathlib import Path

from src.db import init_db

_spec = importlib.util.spec_from_file_location(
    "enregistrer_epoque", Path(__file__).resolve().parent.parent / "scripts" / "enregistrer_epoque_post_correction.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_registers_one_epoch_per_source_and_is_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    assert mod.register(db, "2026-10-29T20:00:00Z") == 5
    assert mod.register(db, "2026-10-30T00:00:00Z") == 0  # idempotent : rien de dupliqué ni modifié
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT source, epoch, started_at FROM hypothesis_epochs ORDER BY source").fetchall()
    conn.close()
    assert len(rows) == 5
    assert {r[1] for r in rows} == {"P1_29-10"} and {r[2] for r in rows} == {"2026-10-29T20:00:00Z"}


def test_main_uses_given_timestamp(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    init_db(db)
    assert mod.main(["x", db, "2026-10-29T21:00:00Z"]) == 0
    assert "2026-10-29T21:00:00Z" in capsys.readouterr().out


def test_register_custom_label_p2_does_not_touch_p1(tmp_path):
    import sqlite3
    from src.db import init_db
    db = str(tmp_path / "t.db")
    init_db(db)
    assert mod.register(db, "2026-10-29T19:03:55Z") == 5
    assert mod.register(db, "2026-10-30T09:00:00Z", "P2_30-10", "p2") == 5
    assert mod.register(db, "2026-10-30T09:00:00Z", "P2_30-10", "p2") == 0
    rows = sqlite3.connect(db).execute("SELECT epoch, COUNT(*) FROM hypothesis_epochs GROUP BY epoch ORDER BY epoch").fetchall()
    assert rows == [("P1_29-10", 5), ("P2_30-10", 5)]
