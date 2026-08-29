import math

import pytest

from src.db import init_db
from src.hypothesis_params import get_active_override
from src.evolution_engine import (
    CandidateSelection,
    CandidateSpec,
    TrainingResult,
    ValidationVerdict,
    bonferroni_one_sided_z,
    build_rule_change_rows,
    compute_calendar_block_bootstrap_lower_bound,
    compute_lower_bound,
    evaluate_validation,
    persist_rule_changes,
    run_evolution_cycle,
    select_best_candidate,
)


# ---------------------------------------------------------------------------
# bonferroni_one_sided_z
# ---------------------------------------------------------------------------

def test_bonferroni_single_candidate_matches_standard_95pct_one_sided():
    assert bonferroni_one_sided_z(1) == pytest.approx(1.6449, abs=1e-3)


def test_bonferroni_three_candidates_matches_published_value():
    # Valeur déjà publiée et utilisée à la main dans docs/HYPOTHESES.md
    # (25/08/2026, cycle 2) — vérifie que la formule reproduit exactement
    # ce qui a été calculé/appliqué manuellement jusqu'ici.
    assert bonferroni_one_sided_z(3) == pytest.approx(2.1285, abs=1e-3)


def test_bonferroni_rejects_zero_or_negative():
    with pytest.raises(ValueError):
        bonferroni_one_sided_z(0)


def test_bonferroni_more_candidates_gives_higher_bar():
    assert bonferroni_one_sided_z(5) > bonferroni_one_sided_z(1)


# ---------------------------------------------------------------------------
# compute_lower_bound
# ---------------------------------------------------------------------------

def test_lower_bound_insufficient_sample_is_none():
    assert compute_lower_bound([], 1.645) is None
    assert compute_lower_bound([0.5], 1.645) is None


def test_lower_bound_below_mean():
    values = [1.0, -0.5, 0.8, -0.2, 1.2]
    z = 1.645
    lb = compute_lower_bound(values, z)
    mean = sum(values) / len(values)
    assert lb < mean


def test_lower_bound_zero_variance():
    values = [0.5, 0.5, 0.5, 0.5]
    assert compute_lower_bound(values, 1.645) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compute_calendar_block_bootstrap_lower_bound
# ---------------------------------------------------------------------------

def test_bootstrap_lower_bound_empty_is_none():
    assert compute_calendar_block_bootstrap_lower_bound([], []) is None


def test_bootstrap_lower_bound_single_block_is_none():
    values = [0.5, -0.2, 0.8]
    timestamps = ["2024-06-01T00:00:00", "2024-06-15T00:00:00", "2024-06-20T00:00:00"]
    assert compute_calendar_block_bootstrap_lower_bound(values, timestamps) is None


def test_bootstrap_lower_bound_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        compute_calendar_block_bootstrap_lower_bound([0.5], ["2024-06-01T00:00:00", "2024-07-01T00:00:00"])


def test_bootstrap_lower_bound_below_mean_with_variance():
    values = [1.0, 0.9, 1.1, -0.8, -0.9, -1.0]
    timestamps = [
        "2024-06-01T00:00:00", "2024-06-10T00:00:00", "2024-06-20T00:00:00",
        "2024-07-01T00:00:00", "2024-07-10T00:00:00", "2024-07-20T00:00:00",
    ]
    lb = compute_calendar_block_bootstrap_lower_bound(values, timestamps, seed=42)
    mean = sum(values) / len(values)
    assert lb < mean


def test_bootstrap_lower_bound_deterministic_with_seed():
    values = [0.5, -0.2, 0.8, 1.1, -0.4, 0.3]
    timestamps = [
        "2024-06-01T00:00:00", "2024-06-15T00:00:00",
        "2024-07-01T00:00:00", "2024-07-15T00:00:00",
        "2024-08-01T00:00:00", "2024-08-15T00:00:00",
    ]
    lb1 = compute_calendar_block_bootstrap_lower_bound(values, timestamps, seed=7)
    lb2 = compute_calendar_block_bootstrap_lower_bound(values, timestamps, seed=7)
    assert lb1 == lb2


def test_bootstrap_lower_bound_zero_variance_matches_constant():
    values = [0.5, 0.5, 0.5, 0.5]
    timestamps = ["2024-06-01T00:00:00", "2024-06-15T00:00:00", "2024-07-01T00:00:00", "2024-07-15T00:00:00"]
    lb = compute_calendar_block_bootstrap_lower_bound(values, timestamps, seed=1)
    assert lb == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# CandidateSpec — invariant #10 : théorie obligatoire si override non vide
# ---------------------------------------------------------------------------

def test_candidate_reference_needs_no_theory():
    CandidateSpec(name="A")  # ne lève pas


def test_candidate_with_override_requires_theory():
    with pytest.raises(ValueError):
        CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="")


def test_candidate_with_override_and_theory_is_valid():
    c = CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="justification")
    assert c.overrides == {"RSI_PERIOD": 9}


# ---------------------------------------------------------------------------
# select_best_candidate
# ---------------------------------------------------------------------------

def test_select_best_candidate_requires_results():
    with pytest.raises(ValueError):
        select_best_candidate([], min_trades=10)


def test_select_best_candidate_none_qualify():
    results = [
        TrainingResult("A", n_trades=5, r_values=(0.1, 0.2)),
        TrainingResult("B", n_trades=200, r_values=(-0.1,) * 200),
    ]
    selection = select_best_candidate(results, min_trades=150)
    assert selection.selected is None
    assert selection.qualifying == ()


def test_select_best_candidate_picks_highest_mean_among_qualifiers():
    strong = tuple([0.5] * 200)
    weak = tuple([0.05] * 200)
    results = [
        TrainingResult("A", n_trades=200, r_values=weak),
        TrainingResult("B", n_trades=200, r_values=strong),
    ]
    selection = select_best_candidate(results, min_trades=150)
    assert selection.selected == "B"
    assert set(selection.qualifying) == {"A", "B"}


def test_select_best_candidate_z_reflects_number_of_candidates():
    results = [TrainingResult("A", n_trades=200, r_values=tuple([0.5] * 200))]
    selection = select_best_candidate(results, min_trades=150)
    assert selection.z_score == pytest.approx(bonferroni_one_sided_z(1), abs=1e-6)


# ---------------------------------------------------------------------------
# evaluate_validation
# ---------------------------------------------------------------------------

def test_validation_fails_on_insufficient_trades():
    v = evaluate_validation(n_trades=10, r_values=[0.5] * 10, min_trades=60, z=1.6449)
    assert v.passed is False


def test_validation_fails_when_lower_bound_undefined_less_than_two_values():
    v = evaluate_validation(n_trades=100, r_values=[0.1], min_trades=60, z=1.6449)
    assert v.passed is False
    assert "N/A" in v.reason


def test_validation_fails_on_non_positive_lower_bound():
    v = evaluate_validation(n_trades=100, r_values=[0.0] * 100, min_trades=60, z=1.6449)
    assert v.passed is False


def test_validation_fails_on_positive_mean_but_negative_lower_bound():
    # Point 8 (29/08/2026) : le gate de puissance rejette desormais une
    # moyenne positive si la variance est trop grande pour la borne basse
    # (comportement impossible avec l'ancien critere "moyenne > 0" seul).
    r_values = [0.5, -0.4] * 50  # moyenne=0.05, ecart-type eleve
    v = evaluate_validation(n_trades=100, r_values=r_values, min_trades=60, z=1.6449)
    assert v.passed is False


def test_validation_passes_when_lower_bound_positive():
    v = evaluate_validation(n_trades=100, r_values=[0.1] * 100, min_trades=60, z=1.6449)
    assert v.passed is True


def test_validation_higher_z_can_flip_a_passing_case_to_failing():
    # Le meme echantillon passe avec z=1.6449 (m=1) mais peut echouer
    # avec un z plus eleve (cumul de validations passees, point 8).
    r_values = [0.5, -0.3] * 50  # moyenne=0.10, ecart-type eleve
    assert evaluate_validation(n_trades=100, r_values=r_values, min_trades=60, z=1.6449).passed is True
    assert evaluate_validation(n_trades=100, r_values=r_values, min_trades=60, z=6.0).passed is False


# ---------------------------------------------------------------------------
# build_rule_change_rows
# ---------------------------------------------------------------------------

def test_build_rows_rejects_reference_candidate():
    with pytest.raises(ValueError):
        build_rule_change_rows("H3", CandidateSpec(name="A"), "constat", "2026-08-26T00:00:00")


def test_build_rows_one_per_param_always_proposed():
    # Point 5 (29/08/2026) : plus de statut 'applique' possible depuis ce
    # module, quel que soit l'appelant - revirement explicite sur
    # l'ecart CDC assume le 25/08/2026 (voir docs/DECISIONS.md).
    candidate = CandidateSpec(name="B", overrides={"TP1_R_MULTIPLE": 0.5, "TP2_R_MULTIPLE": 1.5}, theory="x")
    rows = build_rule_change_rows("H3", candidate, "constat", "2026-08-26T00:00:00")
    assert len(rows) == 2
    variables = {r["variable"] for r in rows}
    assert variables == {"H3.TP1_R_MULTIPLE", "H3.TP2_R_MULTIPLE"}
    assert all(r["statut"] == "propose" for r in rows)
    assert all(r["applied_at"] is None for r in rows)
    assert all(r["validated_at"] is None for r in rows)


# ---------------------------------------------------------------------------
# persist_rule_changes + run_evolution_cycle — orchestration (doubles)
# ---------------------------------------------------------------------------

def test_persist_rule_changes_never_readable_as_active_override(tmp_path):
    # Point 5 (29/08/2026) : une proposition ecrite par ce module n'est
    # JAMAIS active tant qu'un humain n'a pas change son statut a la main
    # (hors de ce module - voir docstring). get_active_override ne lit
    # que statut='applique'.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    rows = build_rule_change_rows(
        "H5", CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x"),
        "constat", "2026-08-26T00:00:00",
    )
    ids = persist_rule_changes(db_path, rows)
    assert len(ids) == 1
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def test_persist_rule_changes_calls_notify_fn_once_per_row_after_commit(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidate = CandidateSpec(name="B", overrides={"TP1_R_MULTIPLE": 0.5, "TP2_R_MULTIPLE": 1.5}, theory="x")
    rows = build_rule_change_rows("H3", candidate, "constat 42 trades, borne basse +0.03R", "2026-08-26T00:00:00")

    notified = []

    def notify_fn(row):
        # Le commit doit deja avoir eu lieu - relisible en base des l'appel.
        assert get_active_override(db_path, "H3", "TP1_R_MULTIPLE") is None  # 'propose', pas 'applique'
        notified.append(row["variable"])

    persist_rule_changes(db_path, rows, notify_fn=notify_fn)
    assert set(notified) == {"H3.TP1_R_MULTIPLE", "H3.TP2_R_MULTIPLE"}


def test_persist_rule_changes_without_notify_fn_is_a_noop_for_notification(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidate = CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x")
    rows = build_rule_change_rows("H5", candidate, "constat", "2026-08-26T00:00:00")
    ids = persist_rule_changes(db_path, rows)  # pas de notify_fn -> ne doit jamais lever
    assert len(ids) == 1


def test_run_evolution_cycle_no_candidate_qualifies(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidates = [
        CandidateSpec(name="A"),
        CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x"),
    ]

    def train_fn(c):
        return TrainingResult(c.name, n_trades=5, r_values=(0.1,))

    def validation_fn(c):
        raise AssertionError("la validation ne doit jamais être appelée si aucun candidat ne qualifie")

    report = run_evolution_cycle(
        "H5", candidates, train_fn, validation_fn, min_trades_train=150, min_trades_validation=60, db_path=db_path,
    )
    assert report.selection.selected is None
    assert report.validation is None
    assert report.applied_rule_change_ids == []


def test_run_evolution_cycle_reference_wins_writes_nothing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidates = [
        CandidateSpec(name="A"),
        CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x"),
    ]

    def train_fn(c):
        # La référence gagne largement, "B" qualifie à peine.
        if c.name == "A":
            return TrainingResult(c.name, n_trades=200, r_values=tuple([0.5] * 200))
        return TrainingResult(c.name, n_trades=200, r_values=tuple([0.01] * 200))

    def validation_fn(c):
        raise AssertionError("aucune validation ne doit être consommée quand la référence gagne")

    report = run_evolution_cycle(
        "H5", candidates, train_fn, validation_fn, min_trades_train=150, min_trades_validation=60, db_path=db_path,
    )
    assert report.selection.selected == "A"
    assert report.validation is None
    assert report.applied_rule_change_ids == []


def test_run_evolution_cycle_validation_fails_writes_nothing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidates = [CandidateSpec(name="A"), CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x")]

    def train_fn(c):
        if c.name == "B":
            return TrainingResult(c.name, n_trades=200, r_values=tuple([0.3] * 200))
        return TrainingResult(c.name, n_trades=200, r_values=tuple([-0.1] * 200))

    def validation_fn(c):
        return TrainingResult(c.name, n_trades=100, r_values=tuple([-0.05] * 100))

    report = run_evolution_cycle(
        "H5", candidates, train_fn, validation_fn, min_trades_train=150, min_trades_validation=60, db_path=db_path,
    )
    assert report.selection.selected == "B"
    assert report.validation.passed is False
    assert report.applied_rule_change_ids == []
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def test_run_evolution_cycle_full_success_writes_propose_never_active(tmp_path):
    # Point 5 (29/08/2026) : un cycle reussi ecrit toujours 'propose',
    # jamais lu comme override actif tant qu'aucune validation humaine
    # n'a change le statut a la main (revirement sur l'ecart CDC du
    # 25/08/2026, voir docs/DECISIONS.md).
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidates = [CandidateSpec(name="A"), CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x")]

    def train_fn(c):
        if c.name == "B":
            return TrainingResult(c.name, n_trades=200, r_values=tuple([0.3] * 200))
        return TrainingResult(c.name, n_trades=200, r_values=tuple([-0.1] * 200))

    def validation_fn(c):
        return TrainingResult(c.name, n_trades=100, r_values=tuple([0.2] * 100))

    report = run_evolution_cycle(
        "H5", candidates, train_fn, validation_fn, min_trades_train=150, min_trades_validation=60,
        db_path=db_path, now_iso="2026-08-26T12:00:00",
    )
    assert report.validation.passed is True
    assert len(report.applied_rule_change_ids) == 1
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def test_run_evolution_cycle_notify_fn_forwarded_to_persist(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidates = [CandidateSpec(name="A"), CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x")]

    def train_fn(c):
        if c.name == "B":
            return TrainingResult(c.name, n_trades=200, r_values=tuple([0.3] * 200))
        return TrainingResult(c.name, n_trades=200, r_values=tuple([-0.1] * 200))

    def validation_fn(c):
        return TrainingResult(c.name, n_trades=100, r_values=tuple([0.2] * 100))

    notified = []
    report = run_evolution_cycle(
        "H5", candidates, train_fn, validation_fn, min_trades_train=150, min_trades_validation=60,
        db_path=db_path, notify_fn=lambda row: notified.append(row["variable"]),
    )
    assert report.applied_rule_change_ids
    assert notified == ["H5.RSI_PERIOD"]


def test_run_evolution_cycle_dry_run_persists_nothing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    candidates = [CandidateSpec(name="A"), CandidateSpec(name="B", overrides={"RSI_PERIOD": 9}, theory="x")]

    def train_fn(c):
        if c.name == "B":
            return TrainingResult(c.name, n_trades=200, r_values=tuple([0.3] * 200))
        return TrainingResult(c.name, n_trades=200, r_values=tuple([-0.1] * 200))

    def validation_fn(c):
        return TrainingResult(c.name, n_trades=100, r_values=tuple([0.2] * 100))

    report = run_evolution_cycle(
        "H5", candidates, train_fn, validation_fn, min_trades_train=150, min_trades_validation=60,
        db_path=db_path, persist=False,
    )
    assert report.validation.passed is True
    assert report.applied_rule_change_ids == []
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None
