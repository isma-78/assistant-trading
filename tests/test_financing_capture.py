"""Tests de financing_capture.py (point 7, 29/08/2026, voir docs/DECISIONS.md)."""

from unittest.mock import MagicMock

from src.db import connection_scope, init_db
from src.financing_capture import capture_recent_financing, parse_swap_transactions


# ---------------------------------------------------------------------------
# parse_swap_transactions
# ---------------------------------------------------------------------------

def test_parse_swap_transactions_filters_non_swap_types():
    raw = [
        {"transactionType": "DEPOSIT", "reference": "1", "instrumentName": "X", "size": "100", "dateUtc": "2026-08-28T21:00:00.000"},
        {"transactionType": "SWAP", "reference": "2", "instrumentName": "USDJPY", "size": "0.23", "dateUtc": "2026-08-28T21:02:07.525"},
    ]
    result = parse_swap_transactions(raw)
    assert len(result) == 1
    assert result[0] == {"reference": "2", "instrument": "USDJPY", "size_eur": 0.23, "date_utc": "2026-08-28T21:02:07.525"}


def test_parse_swap_transactions_converts_size_string_to_float():
    raw = [{"transactionType": "SWAP", "reference": "3", "instrumentName": "GBPUSD", "size": "-0.25", "dateUtc": "2026-08-28T21:01:26.167"}]
    result = parse_swap_transactions(raw)
    assert result[0]["size_eur"] == -0.25
    assert isinstance(result[0]["size_eur"], float)


def test_parse_swap_transactions_empty_input_returns_empty_list():
    assert parse_swap_transactions([]) == []


# ---------------------------------------------------------------------------
# capture_recent_financing
# ---------------------------------------------------------------------------

def test_capture_recent_financing_persists_new_rows(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get.return_value = {"transactions": [
        {"transactionType": "SWAP", "reference": "ref1", "instrumentName": "USDJPY", "size": "0.23", "dateUtc": "2026-08-28T21:02:07.525"},
        {"transactionType": "SWAP", "reference": "ref2", "instrumentName": "GBPUSD", "size": "-0.25", "dateUtc": "2026-08-28T21:01:26.167"},
    ]}

    inserted = capture_recent_financing(client, db_path, now_iso="2026-08-29T00:00:00Z")
    assert inserted == 2
    client.get.assert_called_once_with("/history/transactions?lastPeriod=86400")

    with connection_scope(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM financing_transactions").fetchone()["n"]
    assert n == 2


def test_capture_recent_financing_is_idempotent_on_reference(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get.return_value = {"transactions": [
        {"transactionType": "SWAP", "reference": "ref1", "instrumentName": "USDJPY", "size": "0.23", "dateUtc": "2026-08-28T21:02:07.525"},
    ]}

    first = capture_recent_financing(client, db_path, now_iso="2026-08-29T00:00:00Z")
    second = capture_recent_financing(client, db_path, now_iso="2026-08-29T01:00:00Z")
    assert first == 1
    assert second == 0  # meme reference, deja capturee - jamais un doublon

    with connection_scope(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM financing_transactions").fetchone()["n"]
    assert n == 1


def test_capture_recent_financing_ignores_non_swap_transactions(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get.return_value = {"transactions": [
        {"transactionType": "DEPOSIT", "reference": "ref1", "instrumentName": "X", "size": "500", "dateUtc": "2026-08-29T00:00:00.000"},
    ]}
    assert capture_recent_financing(client, db_path, now_iso="2026-08-29T00:00:00Z") == 0


def test_capture_recent_financing_no_transactions_returns_zero(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get.return_value = {"transactions": []}
    assert capture_recent_financing(client, db_path, now_iso="2026-08-29T00:00:00Z") == 0


# ---------------------------------------------------------------------------
# A4 (bilan du 05/10/2026) : rattrapage par fenêtre + import du script cron
# ---------------------------------------------------------------------------

def test_capture_financing_window_uses_from_to_and_is_idempotent(tmp_path):
    from src.financing_capture import capture_financing_window
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get.return_value = {"transactions": [
        {"transactionType": "SWAP", "reference": "r1", "instrumentName": "EURUSD", "size": "-0.12", "dateUtc": "2026-09-01T21:00:00"},
        {"transactionType": "TRADE", "reference": "r2", "instrumentName": "EURUSD", "size": "5", "dateUtc": "2026-09-01T10:00:00"},
    ]}

    assert capture_financing_window(client, db_path, "2026-09-01T00:00:00", "2026-09-02T00:00:00", "now") == 1
    assert capture_financing_window(client, db_path, "2026-09-01T00:00:00", "2026-09-02T00:00:00", "now") == 0
    client.get.assert_called_with("/history/transactions", params={"from": "2026-09-01T00:00:00", "to": "2026-09-02T00:00:00"})


def test_cron_scripts_import_src_when_launched_from_repo_root(tmp_path):
    """Le cron lance `venv/bin/python scripts/capture_financing.py` : seul
    `scripts/` est alors dans sys.path. Le chargement du script (sans
    exécuter main) ne doit plus lever ModuleNotFoundError."""
    import os
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for script in ("capture_financing.py", "backfill_financing.py"):
        code = f"import runpy; runpy.run_path({os.path.join(root, 'scripts', script)!r}, run_name='not_main')"
        result = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_backfill_day_windows_cover_range_exactly():
    import importlib.util
    import os
    from datetime import datetime, timezone
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "backfill_financing.py")
    spec = importlib.util.spec_from_file_location("backfill_financing", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    start = datetime(2026, 8, 30, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    windows = list(module.day_windows(start, end))
    assert windows[0][0] == start and windows[-1][1] == end and len(windows) == 3
