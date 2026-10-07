"""Tests de scripts/mesure_effet_e2.py (surveillance E2, lecture seule)."""

import importlib.util
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "mesure_effet_e2.py")
_spec = importlib.util.spec_from_file_location("mesure_effet_e2", _PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE logs (timestamp TEXT, level TEXT, module TEXT, message TEXT)")
    conn.execute("CREATE TABLE trades (annulation_motif TEXT, ouvert_at TEXT)")
    conn.execute("CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()
    return conn


def _log(conn, ts, level, message, module="execution.stop_tightening_retry"):
    conn.execute("INSERT INTO logs VALUES (?, ?, ?, ?)", (ts, level, module, message))


SINCE = "2026-10-08T00:00:00"


# --- _parse_field ----------------------------------------------------------

def test_parse_field_found_and_missing():
    msg = "trade_id=42 succeeded=True attempts=2 aborted_on_rate_limit=False fallback_single_attempt=False"
    assert m._parse_field(msg, "attempts") == "2"
    assert m._parse_field(msg, "fallback_single_attempt") == "False"
    assert m._parse_field(msg, "nope") is None


def test_parse_field_last_field_no_trailing_space():
    msg = "trade_id=7 succeeded=True"
    assert m._parse_field(msg, "succeeded") == "True"


# --- _repeated_errors_in_window --------------------------------------------

def test_repeated_errors_below_threshold():
    base = datetime(2026, 10, 8, 0, 0, 0)
    assert m._repeated_errors_in_window([base, base + timedelta(minutes=1)]) is False


def test_repeated_errors_within_window():
    base = datetime(2026, 10, 8, 0, 0, 0)
    ts = [base, base + timedelta(minutes=2), base + timedelta(minutes=4)]
    assert m._repeated_errors_in_window(ts) is True


def test_repeated_errors_empty():
    assert m._repeated_errors_in_window([]) is False


# --- assess -----------------------------------------------------------------

def test_assess_no_data_no_verdict(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.n_attempts == 0 and not result.verdict_possible and result.validated is None
    assert not result.stop_now


def test_assess_excludes_pre_broker_rejections(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    _log(conn, "2026-10-08T01:00:00", "INFO",
         "trade_id=1 succeeded=False attempts=0 aborted_on_rate_limit=False fallback_single_attempt=False")
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.n_attempts == 0


def test_assess_counts_attempts_and_refusals(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    for i in range(40):
        succeeded = "True" if i % 2 == 0 else "False"
        _log(conn, f"2026-10-08T01:{i:02d}:00", "INFO",
             f"trade_id={i} succeeded={succeeded} attempts=1 aborted_on_rate_limit=False fallback_single_attempt=False")
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.n_attempts == 40 and result.n_refusals == 20
    assert result.verdict_possible
    assert result.validated is False  # 0,5 de refus >> seuil de 9,87% -> jamais validé
    assert result.refusal_rate == pytest.approx(0.5)


def test_assess_validated_false_when_rate_too_high(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    for i in range(30):
        _log(conn, f"2026-10-08T01:{i:02d}:00", "INFO",
             f"trade_id={i} succeeded=False attempts=1 aborted_on_rate_limit=False fallback_single_attempt=False")
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.validated is False


def test_assess_ignores_malformed_message_without_fields(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    _log(conn, "2026-10-08T01:00:00", "INFO", "message sans champs structurés")
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.n_attempts == 0


def test_assess_widening_detected_blocks_validation(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    for i in range(30):
        _log(conn, f"2026-10-08T01:{i:02d}:00", "INFO",
             f"trade_id={i} succeeded=True attempts=1 aborted_on_rate_limit=False fallback_single_attempt=False")
    _log(conn, "2026-10-08T02:00:00", "CRITICAL", f"{m.WIDENING_BLOCKED_MARKER} trade_id=99 detail=x")
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.widening_detected and result.validated is None
    assert result.stop_now and "élargissement" in result.stop_reasons[0]


def test_assess_repeated_module_errors_flagged(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    for i in range(3):
        _log(conn, f"2026-10-08T01:0{i}:00", "INFO",
             f"trade_id={i} succeeded=True attempts=1 aborted_on_rate_limit=False fallback_single_attempt=True")
    conn.commit()
    result = m.assess(conn, SINCE)
    assert result.repeated_module_errors and result.stop_now


def test_assess_ignores_message_with_unparseable_timestamp(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    _log(conn, "not-a-date", "INFO",
         "trade_id=1 succeeded=True attempts=1 aborted_on_rate_limit=False fallback_single_attempt=True")
    conn.commit()
    # ne doit pas lever, simplement ignorer l'horodatage invalide pour le calcul des erreurs répétées
    result = m.assess(conn, "")
    assert result.n_attempts == 1


# --- check_429_increase -----------------------------------------------------

def test_check_429_increase_true(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    since = datetime(2026, 10, 8, 10, 0, 0)
    now = since + timedelta(hours=2)
    conn.execute("INSERT INTO trades VALUES ('rate_limit_429', ?)", (since.isoformat(),))
    conn.execute("INSERT INTO trades VALUES ('rate_limit_429', ?)", ((since + timedelta(minutes=30)).isoformat(),))
    conn.commit()
    assert m.check_429_increase(conn, since.isoformat(), now) is True


def test_check_429_increase_false_when_stable(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    since = datetime(2026, 10, 8, 10, 0, 0)
    now = since + timedelta(hours=2)
    reference_ts = (since - timedelta(hours=1)).isoformat()
    conn.execute("INSERT INTO trades VALUES ('rate_limit_429', ?)", (reference_ts,))
    conn.commit()
    assert m.check_429_increase(conn, since.isoformat(), now) is False


def test_check_429_increase_zero_duration_returns_false(tmp_path):
    conn = _make_db(str(tmp_path / "db.sqlite"))
    now = datetime(2026, 10, 8, 10, 0, 0)
    assert m.check_429_increase(conn, now.isoformat(), now) is False


# --- apply_automatic_stop ---------------------------------------------------

def test_apply_automatic_stop_inserts_then_updates(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    conn = _make_db(db_path)
    conn.commit()
    conn.close()
    m.apply_automatic_stop(db_path)
    value = sqlite3.connect(db_path).execute(
        "SELECT value FROM system_state WHERE key='e2_enabled'"
    ).fetchone()[0]
    assert value == "false"
    m.apply_automatic_stop(db_path)  # idempotent, chemin UPDATE
    value2 = sqlite3.connect(db_path).execute(
        "SELECT value FROM system_state WHERE key='e2_enabled'"
    ).fetchone()[0]
    assert value2 == "false"


# --- main --------------------------------------------------------------------

def test_main_no_stop(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    conn = _make_db(db_path)
    conn.commit()
    conn.close()
    since = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    code = m.main(["--db-path", db_path, "--since", since])
    assert code == 0


def test_main_stop_without_apply(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    conn = _make_db(db_path)
    _log(conn, "2026-10-08T01:00:00", "CRITICAL", f"{m.WIDENING_BLOCKED_MARKER} trade_id=1 detail=x")
    conn.commit()
    conn.close()
    code = m.main(["--db-path", db_path, "--since", SINCE])
    assert code == 1
    assert sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM system_state"
    ).fetchone()[0] == 0  # --apply-stop absent : aucune écriture


def test_main_stop_triggered_by_429_increase_only(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    conn = _make_db(db_path)
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    conn.execute("INSERT INTO trades VALUES ('rate_limit_429', ?)", (since.isoformat(),))
    conn.commit()
    conn.close()
    code = m.main(["--db-path", db_path, "--since", since.isoformat()])
    assert code == 1


def test_main_stop_with_apply(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    conn = _make_db(db_path)
    _log(conn, "2026-10-08T01:00:00", "CRITICAL", f"{m.WIDENING_BLOCKED_MARKER} trade_id=1 detail=x")
    conn.commit()
    conn.close()
    code = m.main(["--db-path", db_path, "--since", SINCE, "--apply-stop"])
    assert code == 1
    value = sqlite3.connect(db_path).execute(
        "SELECT value FROM system_state WHERE key='e2_enabled'"
    ).fetchone()[0]
    assert value == "false"
