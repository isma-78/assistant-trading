"""
Tests de process_watchdog.py — logique de détection/alerte, avec
subprocess.run mocké (pgrep n'existe pas sur toutes les plateformes de
développement) et une DB SQLite temporaire réelle pour system_state.
"""

from unittest.mock import MagicMock, patch

from src.db import connection_scope, init_db
from scripts.process_watchdog import PROCESSES, check_process, run_watchdog_check


def _mock_pgrep(alive_modules: set):
    def _run(args, **kwargs):
        module = args[2].split("python -m ", 1)[1]
        result = MagicMock()
        result.returncode = 0 if module in alive_modules else 1
        return result
    return _run


@patch("scripts.process_watchdog.send_notification", return_value=True)
def test_check_process_alive_first_time_no_alert(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("subprocess.run", side_effect=_mock_pgrep({"src.executor"})):
        status = check_process(db_path, "executor_loop", "src.executor", "tok", "chat")
    assert status == "up"
    mock_notify.assert_not_called()


@patch("scripts.process_watchdog.send_notification", return_value=True)
def test_check_process_missing_alerts_once(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("subprocess.run", side_effect=_mock_pgrep(set())):
        status1 = check_process(db_path, "trend_executor", "src.trend_executor", "tok", "chat")
        status2 = check_process(db_path, "trend_executor", "src.trend_executor", "tok", "chat")

    assert status1 == "down"
    assert status2 == "down"
    mock_notify.assert_called_once()  # pas de spam au deuxième passage
    args = mock_notify.call_args[0]
    assert "trend_executor" in args[2]
    assert "Aucun redémarrage automatique" in args[2]


@patch("scripts.process_watchdog.send_notification", return_value=True)
def test_check_process_recovery_notifies_once(mock_notify, tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("subprocess.run", side_effect=_mock_pgrep(set())):
        check_process(db_path, "control_bot", "src.control_bot", "tok", "chat")
    with patch("subprocess.run", side_effect=_mock_pgrep({"src.control_bot"})):
        status = check_process(db_path, "control_bot", "src.control_bot", "tok", "chat")

    assert status == "up"
    assert mock_notify.call_count == 2  # 1 alerte de disparition + 1 de reprise
    recovery_message = mock_notify.call_args[0][2]
    assert "de nouveau actif" in recovery_message
    assert "était arrêté depuis" in recovery_message


@patch("scripts.process_watchdog.send_notification", return_value=True)
def test_check_process_state_persists_across_calls_new_db_connection(mock_notify, tmp_path):
    # Reproduit l'usage réel : chaque exécution cron est un process Python
    # séparé, l'état ne doit dépendre que de la DB, jamais de mémoire.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("subprocess.run", side_effect=_mock_pgrep(set())):
        check_process(db_path, "executor_loop", "src.executor", "tok", "chat")

    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key = 'watchdog:status:executor_loop'").fetchone()
    assert row["value"].startswith("down:")


def test_check_process_without_bot_credentials_never_raises(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("subprocess.run", side_effect=_mock_pgrep(set())):
        status = check_process(db_path, "executor_loop", "src.executor", None, None)
    assert status == "down"


def test_run_watchdog_check_covers_every_known_process(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    config = MagicMock(telegram_bot_token="tok", telegram_chat_id="chat")
    with patch("subprocess.run", side_effect=_mock_pgrep({"src.telegram_listener", "src.executor"})):
        results = run_watchdog_check(config, db_path)

    assert results == {
        "telegram_listener": "up",
        "executor_loop": "up",
        "trend_executor": "down",
        "control_bot": "down",
        "hypothesis3_executor": "down",
        "hypothesis2_executor": "down",
        "hypothesis4_executor": "down",
        "hypothesis5_executor": "down",
    }
    assert set(PROCESSES.keys()) == set(results.keys())
