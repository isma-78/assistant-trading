"""
tests/test_fidelity_continuous.py — Remesure mensuelle de la fidélité
(07/10/2026, étape 4).
"""

import pytest

from src.fidelity_continuous import compute_update, format_first_n20_message, format_monthly_summary_line
from src.fidelity_measurement import FidelityMetrics


def _m(n_paired, mean_abs_gap=0.1, mean_signed_bias=0.05):
    return FidelityMetrics("H1", n_paired + 5, 10, 2, n_paired, 0.8, mean_abs_gap, mean_signed_bias, 2, 3)


def test_compute_update_not_crossed_when_already_above_20():
    update = compute_update("H1", previous_n_paired=22, previous_status="établie", metrics=_m(25))
    assert update.just_crossed_20 is False
    assert update.current_status == "établie"


def test_compute_update_not_crossed_when_still_below_20():
    update = compute_update("H3", previous_n_paired=10, previous_status="non établie (n insuffisant)", metrics=_m(15))
    assert update.just_crossed_20 is False


def test_compute_update_crosses_20_for_the_first_time():
    update = compute_update("H2", previous_n_paired=18, previous_status="non établie (n insuffisant)", metrics=_m(20))
    assert update.just_crossed_20 is True
    assert update.current_n_paired == 20 and update.previous_n_paired == 18


def test_compute_update_status_can_be_established_or_not_after_crossing():
    update_fail = compute_update("H4", 15, "non établie (n insuffisant)", _m(20, mean_abs_gap=0.40))
    assert update_fail.just_crossed_20 is True and update_fail.current_status == "non établie"
    update_ok = compute_update("H4", 15, "non établie (n insuffisant)", _m(20, mean_abs_gap=0.10, mean_signed_bias=0.05))
    assert update_ok.current_status == "établie"


def test_format_first_n20_message_contains_key_facts():
    update = compute_update("H2", 18, "non établie (n insuffisant)", _m(20, mean_abs_gap=0.2, mean_signed_bias=0.1))
    message = format_first_n20_message(update, _m(20, mean_abs_gap=0.2, mean_signed_bias=0.1))
    assert "H2" in message and "18" in message and "établie" in message and "Aucune action automatique" in message


def test_format_monthly_summary_line_contains_counts_and_status():
    update = compute_update("H3", 10, "non établie (n insuffisant)", _m(15))
    line = format_monthly_summary_line(update, _m(15))
    assert "H3" in line and "n=15" in line and "était 10" in line
