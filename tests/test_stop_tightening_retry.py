"""Tests de src/execution/stop_tightening_retry.py (E2, mandat 08/10/2026).
Aucun broker réel : `update_fn` est toujours un double de test."""

import sqlite3
from dataclasses import dataclass

import pytest

from src.execution import stop_tightening_retry as e2


class FakeApiError(Exception):
    pass


@dataclass
class _Decision:
    approved: bool


class ApprovingRiskEngine:
    def evaluate_stop_update(self, current, new, direction):
        return _Decision(approved=True)


class RejectingRiskEngine:
    def evaluate_stop_update(self, current, new, direction):
        return _Decision(approved=False)


def _make_db(path, value=None):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE system_state (key TEXT, value TEXT)")
    if value is not None:
        conn.execute("INSERT INTO system_state VALUES ('e2_enabled', ?)", (value,))
    conn.commit()
    conn.close()


class CallRecorder:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome


# --- is_e2_enabled -------------------------------------------------------

def test_is_e2_enabled_true(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    assert e2.is_e2_enabled(db_path) is True


def test_is_e2_enabled_false_value(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="false")
    assert e2.is_e2_enabled(db_path) is False


def test_is_e2_enabled_no_row(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)
    assert e2.is_e2_enabled(db_path) is False


def test_is_e2_enabled_missing_table(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    sqlite3.connect(db_path).close()
    assert e2.is_e2_enabled(db_path) is False


# --- _is_less_protective --------------------------------------------------

def test_is_less_protective_long():
    assert e2._is_less_protective(99, 100, "long") is True
    assert e2._is_less_protective(101, 100, "long") is False
    assert e2._is_less_protective(100, 100, "long") is False


def test_is_less_protective_short():
    assert e2._is_less_protective(101, 100, "short") is True
    assert e2._is_less_protective(99, 100, "short") is False


def test_is_less_protective_unknown_direction_blocks():
    assert e2._is_less_protective(100, 100, "sideways") is True


# --- attempt_with_retry : E2 OFF (comportement d'avant ce module) -------

def test_e2_off_single_call_success(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="false")
    fn = CallRecorder([None])
    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                                    direction="long", risk_engine=ApprovingRiskEngine())
    assert result.succeeded and result.attempts == 1 and fn.calls == 1


def test_e2_off_propagates_exception(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)  # pas de ligne -> OFF par défaut
    fn = CallRecorder([FakeApiError("boom")])
    with pytest.raises(FakeApiError):
        e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                               direction="long", risk_engine=ApprovingRiskEngine())


# --- attempt_with_retry : E2 ON ------------------------------------------

def test_e2_on_rejected_by_risk_engine_never_calls_update(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([])
    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=100.0, target_stop=101.0,
                                    direction="long", risk_engine=RejectingRiskEngine())
    assert not result.succeeded and result.attempts == 0 and result.final_stop == 100.0 and fn.calls == 0


def test_e2_on_success_first_attempt(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([None])
    sleeps = []
    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                                    direction="long", risk_engine=ApprovingRiskEngine(), sleep_fn=sleeps.append)
    assert result.succeeded and result.attempts == 1 and result.final_stop == 100.0 and sleeps == []


def test_e2_on_success_after_one_retry(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([FakeApiError("transient"), None])
    sleeps = []
    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                                    direction="long", risk_engine=ApprovingRiskEngine(), sleep_fn=sleeps.append)
    assert result.succeeded and result.attempts == 2 and sleeps == [2.0]


def test_e2_on_exhausts_all_attempts(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([FakeApiError("1"), FakeApiError("2"), FakeApiError("3")])
    sleeps = []
    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                                    direction="long", risk_engine=ApprovingRiskEngine(), sleep_fn=sleeps.append)
    assert not result.succeeded and result.attempts == 3 and result.final_stop == 99.0
    assert sleeps == [2.0, 5.0]
    assert fn.calls == 3


def test_e2_on_aborts_immediately_on_429(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([FakeApiError("error.too-many.requests 429")])
    sleeps = []
    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                                    direction="long", risk_engine=ApprovingRiskEngine(), sleep_fn=sleeps.append)
    assert not result.succeeded and result.attempts == 1 and result.aborted_on_rate_limit
    assert result.final_stop == 99.0 and sleeps == []


def test_e2_on_stop_widening_blocked_even_if_risk_engine_wrongly_approves(tmp_path):
    """Défense en profondeur : même si le `risk_engine` fourni approuve
    (bug hypothétique), le module bloque lui-même toute cible moins
    protectrice que la précédente de cette séquence — jamais contourné."""
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([])
    with pytest.raises(e2.StopWideningBlocked):
        e2.attempt_with_retry(fn, db_path=db_path, current_stop=100.0, target_stop=95.0,
                               direction="long", risk_engine=ApprovingRiskEngine())
    assert fn.calls == 0


def test_e2_on_logs_outcome_when_logs_table_exists(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE system_state (key TEXT, value TEXT)")
    conn.execute("INSERT INTO system_state VALUES ('e2_enabled', 'true')")
    conn.execute("CREATE TABLE logs (timestamp TEXT, level TEXT, module TEXT, message TEXT)")
    conn.commit()
    conn.close()

    fn = CallRecorder([None])
    e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                           direction="long", risk_engine=ApprovingRiskEngine(), trade_id=42)

    rows = sqlite3.connect(db_path).execute("SELECT module, message FROM logs").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "execution.stop_tightening_retry"
    assert "trade_id=42" in rows[0][1] and "succeeded=True" in rows[0][1]


def test_e2_on_unexpected_module_error_falls_back_to_single_attempt(tmp_path):
    """Une erreur qui n'est PAS une erreur broker normale (ici :
    `sleep_fn` lui-même qui casse) retombe sur un seul appel
    supplémentaire, comportement d'avant E2, jamais un crash."""
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, value="true")
    fn = CallRecorder([FakeApiError("transient"), None])

    def broken_sleep(_delay):
        raise RuntimeError("bug du module, jamais une erreur broker")

    result = e2.attempt_with_retry(fn, db_path=db_path, current_stop=99.0, target_stop=100.0,
                                    direction="long", risk_engine=ApprovingRiskEngine(), sleep_fn=broken_sleep)
    assert result.succeeded and result.attempts == 1 and result.fallback_single_attempt
    assert fn.calls == 2  # 1er essai (échoue, déclenche le bug), puis le repli (réussit)
