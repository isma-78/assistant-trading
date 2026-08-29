"""Tests de evolution_cycle_controller.py (point 8, 29/08/2026, voir docs/DECISIONS.md)."""

import pytest

from src.db import init_db
from src.evolution_engine import CandidateSpec, TrainingResult
from src.evolution_cycle_controller import (
    count_recent_validations,
    cumulative_validation_count,
    is_deadline_exceeded,
    is_evolution_cycle_enabled,
    record_validation,
    run_all_enabled_cycles,
    run_guarded_evolution_cycle,
    set_evolution_cycle_enabled,
)


# ---------------------------------------------------------------------------
# is_deadline_exceeded (pur)
# ---------------------------------------------------------------------------

def test_is_deadline_exceeded_none_budget_never_exceeded():
    assert is_deadline_exceeded(start_time=0.0, max_wall_seconds=None, now=1_000_000.0) is False


def test_is_deadline_exceeded_false_within_budget():
    assert is_deadline_exceeded(start_time=100.0, max_wall_seconds=60.0, now=130.0) is False


def test_is_deadline_exceeded_true_at_or_past_budget():
    assert is_deadline_exceeded(start_time=100.0, max_wall_seconds=60.0, now=160.0) is True
    assert is_deadline_exceeded(start_time=100.0, max_wall_seconds=60.0, now=200.0) is True


# ---------------------------------------------------------------------------
# interrupteur DB
# ---------------------------------------------------------------------------

def test_evolution_cycle_enabled_by_default_when_no_row(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert is_evolution_cycle_enabled(db_path, "H1_v2") is True


def test_set_and_read_evolution_cycle_disabled(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    set_evolution_cycle_enabled(db_path, "H1_v2", False)
    assert is_evolution_cycle_enabled(db_path, "H1_v2") is False
    # Les autres hypotheses restent activees par defaut (pas d'effet croise).
    assert is_evolution_cycle_enabled(db_path, "H2_v2") is True


def test_set_evolution_cycle_enabled_upsert_overwrites_previous_value(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    set_evolution_cycle_enabled(db_path, "H1_v2", False)
    set_evolution_cycle_enabled(db_path, "H1_v2", True)
    assert is_evolution_cycle_enabled(db_path, "H1_v2") is True


# ---------------------------------------------------------------------------
# plafond de validations / compteur cumule
# ---------------------------------------------------------------------------

def test_cumulative_validation_count_zero_when_none_recorded(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert cumulative_validation_count(db_path, "H1_v2") == 0


def test_record_and_cumulative_count(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    record_validation(db_path, "H1_v2", "2026-07-01T00:00:00Z")
    record_validation(db_path, "H1_v2", "2026-08-01T00:00:00Z")
    record_validation(db_path, "H2_v2", "2026-08-01T00:00:00Z")  # autre hypothese, ne doit pas compter
    assert cumulative_validation_count(db_path, "H1_v2") == 2
    assert cumulative_validation_count(db_path, "H2_v2") == 1


def test_count_recent_validations_excludes_entries_older_than_window(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    record_validation(db_path, "H1_v2", "2026-01-01T00:00:00Z")  # tres ancien
    record_validation(db_path, "H1_v2", "2026-08-15T00:00:00Z")  # < 30j avant le 29/08
    assert count_recent_validations(db_path, "H1_v2", now_iso="2026-08-29T00:00:00Z") == 1


def test_count_recent_validations_zero_when_none(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert count_recent_validations(db_path, "H1_v2", now_iso="2026-08-29T00:00:00Z") == 0


def test_count_recent_validations_accepts_mixed_iso_formats(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    record_validation(db_path, "H1_v2", "2026-08-16T19:03:27.857413+00:00")
    assert count_recent_validations(db_path, "H1_v2", now_iso="2026-08-29T00:00:00Z") == 1


# ---------------------------------------------------------------------------
# run_guarded_evolution_cycle
# ---------------------------------------------------------------------------

def _candidates():
    return [CandidateSpec(name="A"), CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x")]


def _winning_train_fn(c):
    if c.name == "B":
        return TrainingResult(c.name, n_trades=200, r_values=tuple([0.3] * 200))
    return TrainingResult(c.name, n_trades=200, r_values=tuple([-0.1] * 200))


def _passing_validation_fn(c):
    return TrainingResult(c.name, n_trades=100, r_values=tuple([0.2] * 100))


def test_guarded_cycle_skipped_when_disabled(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    set_evolution_cycle_enabled(db_path, "H5_v2", False)

    def must_not_run(c):
        raise AssertionError("train_fn ne doit jamais etre appelee, cycle desactive")

    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), must_not_run, must_not_run,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
    )
    assert outcome.ran is False
    assert "sactiv" in outcome.reason


def test_guarded_cycle_skipped_when_monthly_cap_already_reached(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    record_validation(db_path, "H5_v2", "2026-08-20T00:00:00Z")  # deja consommee ce mois-ci

    def must_not_run(c):
        raise AssertionError("train_fn ne doit jamais etre appelee, plafond deja atteint")

    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), must_not_run, must_not_run,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
    )
    assert outcome.ran is False
    assert "Plafond" in outcome.reason


def test_guarded_cycle_success_records_validation_and_bumps_cumulative_count(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), _winning_train_fn, _passing_validation_fn,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
    )
    assert outcome.ran is True
    assert outcome.report.validation.passed is True
    assert cumulative_validation_count(db_path, "H5_v2") == 1


def test_guarded_cycle_reference_wins_never_records_a_validation(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)

    def reference_wins_train_fn(c):
        if c.name == "A":
            return TrainingResult(c.name, n_trades=200, r_values=tuple([0.5] * 200))
        return TrainingResult(c.name, n_trades=200, r_values=tuple([0.01] * 200))

    def must_not_run(c):
        raise AssertionError("validation_fn ne doit jamais etre appelee quand la reference gagne")

    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), reference_wins_train_fn, must_not_run,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
    )
    assert outcome.ran is True
    assert outcome.report.validation is None
    assert cumulative_validation_count(db_path, "H5_v2") == 0


def test_guarded_cycle_uses_cumulative_count_to_raise_z(tmp_path):
    # Avec 2 validations deja consommees dans le passe, z doit etre
    # strictement plus eleve qu'un premier essai (m=3 au lieu de m=1) -
    # verifie via la raison rapportee (contient le z applique).
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    record_validation(db_path, "H5_v2", "2026-01-01T00:00:00Z")
    record_validation(db_path, "H5_v2", "2026-02-01T00:00:00Z")

    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), _winning_train_fn, _passing_validation_fn,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
    )
    assert outcome.ran is True
    assert "z=2." in outcome.report.validation.reason or "z=3." in outcome.report.validation.reason


def test_guarded_cycle_deadline_exceeded_before_start(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)

    def must_not_run(c):
        raise AssertionError("aucun entrainement ne doit demarrer, budget deja epuise")

    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), must_not_run, must_not_run,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
        max_wall_seconds=10.0, start_time=0.0,
    )
    assert outcome.ran is False
    assert "Budget" in outcome.reason


def test_guarded_cycle_deadline_exceeded_mid_training(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    calls = []

    def slow_train_fn(c):
        calls.append(c.name)
        if len(calls) >= 2:
            # Simule le temps qui passe reellement entre deux candidats.
            import time
            time.sleep(0.05)
        return TrainingResult(c.name, n_trades=200, r_values=tuple([0.1] * 200))

    outcome = run_guarded_evolution_cycle(
        "H5_v2", _candidates(), slow_train_fn, _passing_validation_fn,
        min_trades_train=150, min_trades_validation=60, db_path=db_path, now_iso="2026-08-29T00:00:00Z",
        max_wall_seconds=0.001,
    )
    assert outcome.ran is False
    assert "Budget" in outcome.reason


# ---------------------------------------------------------------------------
# run_all_enabled_cycles
# ---------------------------------------------------------------------------

def test_run_all_enabled_cycles_isolates_per_hypothesis_failure(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)

    def crashing_train_fn(c):
        raise RuntimeError("panne simulee")

    hypotheses = {
        "H1_v2": {
            "candidates": _candidates(), "train_fn": crashing_train_fn, "validation_fn": _passing_validation_fn,
            "min_trades_train": 150, "min_trades_validation": 60,
        },
        "H2_v2": {
            "candidates": _candidates(), "train_fn": _winning_train_fn, "validation_fn": _passing_validation_fn,
            "min_trades_train": 150, "min_trades_validation": 60,
        },
    }
    results = run_all_enabled_cycles(hypotheses, db_path, now_iso="2026-08-29T00:00:00Z")
    assert results["H1_v2"].ran is False
    assert "panne simulee" in results["H1_v2"].reason
    assert results["H2_v2"].ran is True
    assert results["H2_v2"].report.validation.passed is True


def test_run_all_enabled_cycles_respects_shared_wall_budget_across_hypotheses(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    hypotheses = {
        "H1_v2": {
            "candidates": _candidates(),
            "train_fn": lambda c: (_ for _ in ()).throw(AssertionError("budget deja epuise avant ce point")),
            "validation_fn": _passing_validation_fn,
            "min_trades_train": 150, "min_trades_validation": 60,
        },
    }
    results = run_all_enabled_cycles(hypotheses, db_path, now_iso="2026-08-29T00:00:00Z", max_wall_seconds=0.0)
    assert results["H1_v2"].ran is False
    assert "Budget" in results["H1_v2"].reason
