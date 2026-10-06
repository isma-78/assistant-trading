"""
tests/test_fidelity_measurement.py — Mesure de la fidélité H1-H4
(07/10/2026, voir docs/PROTOCOLE_FIDELITE_07-10.md).
"""

from datetime import datetime

import pytest

from src.evolution_v2_test import TradeRef
from src.fidelity_measurement import (
    MAX_MEAN_ABS_GAP,
    MIN_N_ESTABLISHED,
    compute_fidelity_metrics,
    estimate_date_n20,
    fidelity_status,
    gap_share_explained,
    should_model_cause,
    tpfixe_counterfactual_r,
)


def _t(asset, direction, ts, r):
    return TradeRef(asset=asset, direction=direction, entry_time_utc=ts, r_multiple=r)


# ---------------------------------------------------------------------------
# compute_fidelity_metrics
# ---------------------------------------------------------------------------

def test_compute_fidelity_metrics_perfect_match():
    live = [_t("EURUSD", "long", "2026-09-01T10:00:00", 1.0), _t("GOLD", "short", "2026-09-02T10:00:00", -1.0)]
    backtest = [_t("EURUSD", "long", "2026-09-01T10:30:00", 1.0), _t("GOLD", "short", "2026-09-02T10:30:00", -1.0)]
    m = compute_fidelity_metrics("H1", live, backtest, n_out_of_scope=5, n_unreconciled=2)
    assert m.n_live_eligible == 2 and m.n_paired == 2 and m.pairing_rate == 1.0
    assert m.mean_abs_gap == pytest.approx(0.0) and m.mean_signed_bias == pytest.approx(0.0)
    assert m.n_unmatched_live == 0 and m.n_unmatched_backtest == 0
    assert m.n_live_out_of_scope == 5 and m.n_live_unreconciled == 2


def test_compute_fidelity_metrics_signed_bias_direction():
    """biais = R_live − R_backtest (positif = le live a fait mieux)."""
    live = [_t("EURUSD", "long", "2026-09-01T10:00:00", 1.0)]
    backtest = [_t("EURUSD", "long", "2026-09-01T10:00:00", 0.4)]
    m = compute_fidelity_metrics("H1", live, backtest)
    assert m.mean_signed_bias == pytest.approx(0.6)
    assert m.mean_abs_gap == pytest.approx(0.6)


def test_compute_fidelity_metrics_unmatched_live_excluded_from_gap():
    live = [
        _t("EURUSD", "long", "2026-09-01T10:00:00", 1.0),
        _t("GBPUSD", "long", "2026-09-05T10:00:00", -1.0),  # pas de pendant backtest
    ]
    backtest = [_t("EURUSD", "long", "2026-09-01T10:30:00", 0.8)]
    m = compute_fidelity_metrics("H1", live, backtest)
    assert m.n_paired == 1 and m.n_unmatched_live == 1
    assert m.mean_abs_gap == pytest.approx(0.2)  # seule la paire EURUSD compte, jamais 0-(-1.0)


def test_compute_fidelity_metrics_backtest_only_trade_counted_separately():
    live = [_t("EURUSD", "long", "2026-09-01T10:00:00", 1.0)]
    backtest = [_t("EURUSD", "long", "2026-09-01T10:00:00", 1.0), _t("GOLD", "short", "2026-09-03T00:00:00", 2.0)]
    m = compute_fidelity_metrics("H1", live, backtest)
    assert m.n_unmatched_backtest == 1 and m.n_paired == 1


def test_compute_fidelity_metrics_no_live_trades():
    m = compute_fidelity_metrics("H4", [], [])
    assert m.pairing_rate is None and m.mean_abs_gap is None and m.mean_signed_bias is None


def test_compute_fidelity_metrics_duplicate_valued_trades_not_confused():
    """Deux trades live identiques en valeur (même actif/sens/R) à des
    horodatages différents : chacun doit s'apparier à SON propre
    pendant backtest, jamais l'un à la place de l'autre (identité
    d'objet, pas égalité de valeur)."""
    live = [_t("EURUSD", "long", "2026-09-01T10:00:00", 1.0), _t("EURUSD", "long", "2026-09-05T10:00:00", 1.0)]
    backtest = [_t("EURUSD", "long", "2026-09-01T10:30:00", 1.0)]  # seul le 1er a un pendant
    m = compute_fidelity_metrics("H1", live, backtest)
    assert m.n_paired == 1 and m.n_unmatched_live == 1 and m.mean_abs_gap == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# fidelity_status
# ---------------------------------------------------------------------------

def test_fidelity_status_insufficient_n():
    from src.fidelity_measurement import FidelityMetrics
    m = FidelityMetrics("H2", 10, 0, 0, 10, 1.0, 0.1, 0.05, 0, 0)
    assert fidelity_status(m) == "non établie (n insuffisant)"


def test_fidelity_status_established_when_both_thresholds_met():
    from src.fidelity_measurement import FidelityMetrics
    m = FidelityMetrics("H1", 25, 0, 0, 25, 1.0, 0.25, 0.10, 0, 0)
    assert fidelity_status(m) == "établie"


def test_fidelity_status_not_established_when_gap_too_large():
    from src.fidelity_measurement import FidelityMetrics
    m = FidelityMetrics("H1", 25, 0, 0, 25, 1.0, 0.31, 0.05, 0, 0)
    assert fidelity_status(m) == "non établie"


def test_fidelity_status_insufficient_when_metrics_constructed_without_gap_despite_enough_n():
    """Garde défensive : un appelant pourrait construire `FidelityMetrics`
    directement (pas via `compute_fidelity_metrics`) avec n_paired>=20
    mais sans gap calculé."""
    from src.fidelity_measurement import FidelityMetrics
    m = FidelityMetrics("H1", 25, 0, 0, 25, 1.0, None, None, 0, 0)
    assert fidelity_status(m) == "non établie (n insuffisant)"


def test_fidelity_status_not_established_when_bias_too_large():
    from src.fidelity_measurement import FidelityMetrics
    m = FidelityMetrics("H1", 25, 0, 0, 25, 1.0, 0.10, 0.16, 0, 0)
    assert fidelity_status(m) == "non établie"


def test_fidelity_status_boundary_values_pass():
    from src.fidelity_measurement import FidelityMetrics
    m = FidelityMetrics("H1", 20, 0, 0, 20, 1.0, MAX_MEAN_ABS_GAP, 0.15, 0, 0)
    assert fidelity_status(m) == "établie"
    assert fidelity_status(FidelityMetrics("H1", 19, 0, 0, 19, 1.0, 0.0, 0.0, 0, 0)) == "non établie (n insuffisant)"


# ---------------------------------------------------------------------------
# estimate_date_n20
# ---------------------------------------------------------------------------

def test_estimate_date_n20_returns_none_when_already_established():
    assert estimate_date_n20(25, "2026-09-01T00:00:00", "2026-09-20T00:00:00", datetime(2026, 9, 25)) is None


def test_estimate_date_n20_projects_linearly():
    # 6 paires en 10 jours (2026-09-01 -> 2026-09-11) -> taux = 5/10 = 0.5 paire/jour.
    # Besoin de 14 paires de plus -> 28 jours depuis "now".
    now = datetime(2026, 9, 25)
    estimate = estimate_date_n20(6, "2026-09-01T00:00:00", "2026-09-11T00:00:00", now)
    assert estimate == "2026-10-23"


def test_estimate_date_n20_none_when_no_progress_or_insufficient_data():
    now = datetime(2026, 9, 25)
    assert estimate_date_n20(1, "2026-09-01T00:00:00", "2026-09-01T00:00:00", now) is None
    assert estimate_date_n20(3, None, None, now) is None
    assert estimate_date_n20(3, "2026-09-01T00:00:00", "2026-09-01T00:00:00", now) is None  # span nul


# ---------------------------------------------------------------------------
# tpfixe_counterfactual_r
# ---------------------------------------------------------------------------

def test_tpfixe_counterfactual_uses_first_leg_only():
    assert tpfixe_counterfactual_r([(0.5, 1.0), (0.3, 2.0), (0.2, 0.0)]) == pytest.approx(1.0)


def test_tpfixe_counterfactual_matches_actual_for_single_leg_stop():
    assert tpfixe_counterfactual_r([(1.0, -1.0)]) == pytest.approx(-1.0)


def test_tpfixe_counterfactual_none_when_no_partials():
    assert tpfixe_counterfactual_r([]) is None


# ---------------------------------------------------------------------------
# gap_share_explained / should_model_cause
# ---------------------------------------------------------------------------

def test_gap_share_explained_basic():
    contributions = [0.1, 0.2, None]
    gaps = [0.5, 0.5, 0.5]
    assert gap_share_explained(contributions, gaps) == pytest.approx(0.3 / 1.5)


def test_gap_share_explained_none_when_no_measurable_contribution():
    assert gap_share_explained([None, None], [0.5, 0.5]) is None


def test_gap_share_explained_none_when_no_gaps():
    assert gap_share_explained([0.1], []) is None
    assert gap_share_explained([0.1], [0.0, 0.0]) is None


def test_should_model_cause_threshold():
    assert should_model_cause(0.20) is True
    assert should_model_cause(0.1999) is False
    assert should_model_cause(None) is False
