"""
tests/test_evolution_v2_test.py — Moteur du walk-forward des candidates
V2 (06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §5).
"""

import pytest

from src.evolution_v2_test import (
    FoldResult,
    TradeRef,
    compute_mde,
    decide_walk_forward,
    evaluate_fold,
    one_sample_block_bootstrap_lower_bound,
    pair_candidate_against_baseline,
    select_best_grid_value,
)


def _t(asset, direction, ts, r):
    return TradeRef(asset=asset, direction=direction, entry_time_utc=ts, r_multiple=r)


# ---------------------------------------------------------------------------
# select_best_grid_value
# ---------------------------------------------------------------------------

def test_select_best_grid_value_eliminates_under_n_min():
    results = {1: (199, 0.5), 2: (250, 0.1), 3: (300, 0.3)}
    assert select_best_grid_value(results) == 3  # 1 éliminé (n<200), 3 > 2 en espérance brute


def test_select_best_grid_value_none_when_all_eliminated():
    assert select_best_grid_value({1: (50, 0.5), 2: (10, 0.9)}) is None


def test_select_best_grid_value_ignores_none_mean():
    assert select_best_grid_value({1: (300, None), 2: (300, 0.2)}) == 2


# ---------------------------------------------------------------------------
# pair_candidate_against_baseline
# ---------------------------------------------------------------------------

def test_pairing_matches_same_asset_direction_within_tolerance():
    baseline = [_t("EURUSD", "long", "2021-03-01T10:00:00", -1.0)]
    candidate = [_t("EURUSD", "long", "2021-03-01T11:30:00", 2.0)]
    diffs, unmatched_base, unmatched_cand = pair_candidate_against_baseline(baseline, candidate)
    assert diffs == [3.0]  # 2.0 - (-1.0)
    assert unmatched_base == [] and unmatched_cand == []


def test_pairing_unmatched_baseline_counts_candidate_as_zero():
    baseline = [_t("EURUSD", "long", "2021-03-01T10:00:00", -1.0)]
    diffs, unmatched_base, unmatched_cand = pair_candidate_against_baseline(baseline, [])
    assert diffs == [0.0 - (-1.0)]
    assert unmatched_base == baseline and unmatched_cand == []


def test_pairing_rejects_match_beyond_tolerance():
    baseline = [_t("EURUSD", "long", "2021-03-01T10:00:00", -1.0)]
    candidate = [_t("EURUSD", "long", "2021-03-01T13:00:00", 2.0)]  # 3h, > 2h
    diffs, unmatched_base, unmatched_cand = pair_candidate_against_baseline(baseline, candidate)
    assert diffs == [1.0]  # candidate traité comme non signalé (0) : 0 - (-1.0)
    assert unmatched_base == baseline
    assert unmatched_cand == candidate


def test_pairing_rejects_mismatched_asset_or_direction():
    baseline = [_t("EURUSD", "long", "2021-03-01T10:00:00", -1.0)]
    wrong_asset = [_t("GBPUSD", "long", "2021-03-01T10:00:00", 5.0)]
    wrong_dir = [_t("EURUSD", "short", "2021-03-01T10:00:00", 5.0)]
    diffs1, _, _ = pair_candidate_against_baseline(baseline, wrong_asset)
    diffs2, _, _ = pair_candidate_against_baseline(baseline, wrong_dir)
    assert diffs1 == [1.0] and diffs2 == [1.0]


def test_pairing_picks_closest_candidate_and_consumes_it_once():
    baseline = [
        _t("EURUSD", "long", "2021-03-01T10:00:00", -1.0),
        _t("EURUSD", "long", "2021-03-01T10:30:00", -1.0),
    ]
    candidate = [_t("EURUSD", "long", "2021-03-01T10:05:00", 2.0)]  # proche du 1er, loin du 2e (35min, dans tolérance aussi)
    diffs, unmatched_base, unmatched_cand = pair_candidate_against_baseline(baseline, candidate)
    # Le candidat unique est consommé par le match le plus proche (le premier) ; le second reste non apparié.
    assert diffs == [3.0, 1.0]
    assert len(unmatched_base) == 1 and unmatched_base[0].entry_time_utc == "2021-03-01T10:30:00"
    assert unmatched_cand == []


def test_pairing_candidate_without_baseline_counterpart_reported_separately():
    baseline = []
    candidate = [_t("EURUSD", "long", "2021-03-01T10:00:00", 2.0)]
    diffs, unmatched_base, unmatched_cand = pair_candidate_against_baseline(baseline, candidate)
    assert diffs == [] and unmatched_base == [] and unmatched_cand == candidate


# ---------------------------------------------------------------------------
# one_sample_block_bootstrap_lower_bound
# ---------------------------------------------------------------------------

def test_bootstrap_lower_bound_empty_series_returns_none():
    assert one_sample_block_bootstrap_lower_bound([], 0.0125) is None


def test_bootstrap_lower_bound_positive_series_gives_positive_lower_bound():
    series = [(f"2021-{m:02d}-03", 1.0) for m in range(1, 13)]
    lower = one_sample_block_bootstrap_lower_bound(series, 0.05, reps=500)
    assert lower == pytest.approx(1.0)  # série constante : toute borne = 1.0


def test_bootstrap_lower_bound_reproducible_with_same_seed():
    series = [(f"2021-{m:02d}-{d:02d}", (m + d) % 3 - 1) for m in range(1, 13) for d in (3, 17)]
    a = one_sample_block_bootstrap_lower_bound(series, 0.0125, reps=200, seed=42)
    b = one_sample_block_bootstrap_lower_bound(series, 0.0125, reps=200, seed=42)
    assert a == b


# ---------------------------------------------------------------------------
# compute_mde
# ---------------------------------------------------------------------------

def test_compute_mde_none_below_two_samples():
    assert compute_mde(1.0, 1) is None
    assert compute_mde(1.0, 0) is None


def test_compute_mde_scales_with_sigma_and_inversely_with_sqrt_n():
    mde_40 = compute_mde(1.0, 40)
    mde_160 = compute_mde(1.0, 160)
    assert mde_40 > 0
    assert mde_160 == pytest.approx(mde_40 / 2, rel=1e-6)  # sqrt(160)=2*sqrt(40)
    assert compute_mde(2.0, 40) == pytest.approx(2 * mde_40, rel=1e-6)


# ---------------------------------------------------------------------------
# evaluate_fold
# ---------------------------------------------------------------------------

def test_evaluate_fold_empty_baseline():
    result = evaluate_fold("2021", [], [])
    assert result == FoldResult("2021", 0, None, None, None, None, 0, 0)


def test_evaluate_fold_computes_means_and_diff():
    baseline = [_t("EURUSD", "long", "2021-01-01T00:00:00", -1.0), _t("EURUSD", "long", "2021-02-01T00:00:00", 1.0)]
    candidate = [_t("EURUSD", "long", "2021-01-01T00:30:00", 2.0)]
    result = evaluate_fold("2021", baseline, candidate)
    assert result.n_test == 2
    assert result.mean_baseline == pytest.approx(0.0)
    # diffs : [2.0-(-1.0), 0-1.0] = [3.0, -1.0] -> diff moyen = 1.0
    assert result.diff == pytest.approx(1.0)
    assert result.mean_candidate == pytest.approx(1.0)
    assert result.n_unmatched_baseline == 1
    assert result.n_unmatched_candidate == 0


# ---------------------------------------------------------------------------
# decide_walk_forward
# ---------------------------------------------------------------------------

def _fold(label, n, diff, s_diff):
    return FoldResult(label, n, 0.0, diff, diff, s_diff, 0, 0)


def test_decide_validated_when_all_conditions_met():
    fold_a = _fold("2021", 150, 0.3, 1.0)
    fold_b = _fold("2022", 150, 0.25, 1.0)
    verdict = decide_walk_forward("H1", fold_a, fold_b, lower_bound=0.05, expected_effect=0.24, fidelity_reliable=True)
    assert verdict.status == "validée"
    assert verdict.n_test_pooled == 300


def test_decide_non_validated_when_sign_flips_between_folds():
    fold_a = _fold("2021", 150, 0.3, 1.0)
    fold_b = _fold("2022", 150, -0.1, 1.0)
    verdict = decide_walk_forward("H4", fold_a, fold_b, lower_bound=0.5, expected_effect=0.10, fidelity_reliable=True)
    assert verdict.status in ("non validée", "indémontrable sur cette fenêtre")


def test_decide_indemontrable_when_mde_exceeds_expected_effect():
    fold_a = _fold("2021", 30, 0.05, 2.0)
    fold_b = _fold("2022", 30, 0.05, 2.0)
    verdict = decide_walk_forward("H2", fold_a, fold_b, lower_bound=-0.1, expected_effect=0.13, fidelity_reliable=True)
    assert verdict.status == "indémontrable sur cette fenêtre"
    assert verdict.mde > 0.13


def test_decide_non_validated_when_lower_bound_not_positive():
    fold_a = _fold("2021", 300, 0.3, 0.5)
    fold_b = _fold("2022", 300, 0.3, 0.5)
    verdict = decide_walk_forward("H3", fold_a, fold_b, lower_bound=-0.01, expected_effect=0.13, fidelity_reliable=True)
    assert verdict.status != "validée"


def test_decide_non_validated_when_fidelity_unreliable_even_if_otherwise_validated():
    fold_a = _fold("2021", 300, 0.3, 0.5)
    fold_b = _fold("2022", 300, 0.3, 0.5)
    verdict = decide_walk_forward("H3", fold_a, fold_b, lower_bound=0.05, expected_effect=0.13, fidelity_reliable=False)
    assert verdict.status != "validée"


def test_decide_non_validated_when_a_fold_is_empty():
    fold_a = FoldResult("2021", 0, None, None, None, None, 0, 0)
    fold_b = _fold("2022", 300, 0.3, 0.5)
    verdict = decide_walk_forward("H1", fold_a, fold_b, lower_bound=0.5, expected_effect=0.24, fidelity_reliable=True)
    assert verdict.status == "non validée"
    assert verdict.n_test_pooled == 300


def test_decide_handles_n_pooled_exactly_two_without_division_by_zero():
    fold_a = _fold("2021", 1, 0.3, 0.0)
    fold_b = _fold("2022", 1, 0.3, 0.0)
    verdict = decide_walk_forward("H1", fold_a, fold_b, lower_bound=0.1, expected_effect=0.24, fidelity_reliable=True)
    assert verdict.n_test_pooled == 2
