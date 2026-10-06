"""
tests/test_shadow_milestone.py — Jalons/alertes/verdict forward shadow
(06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §6-7).
"""

from datetime import datetime, timezone

import pytest

from src.shadow_milestone import (
    MIN_WEEKS_SINCE_T0,
    compute_status,
    eligible_milestones,
    format_milestone_message,
    format_verdict_message,
    pending_alerts,
    weeks_since,
)
from src.evolution_v2_test import ForwardVerdict


def test_weeks_since_basic():
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert weeks_since("2026-08-15T00:00:00+00:00", now) == pytest.approx(8.0, abs=0.2)


def test_weeks_since_handles_naive_timestamp():
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert weeks_since("2026-08-15T00:00:00", now) == pytest.approx(8.0, abs=0.2)


def test_eligible_milestones_requires_both_conditions():
    assert eligible_milestones(60, weeks_elapsed=7.9) == []  # pas assez de semaines, même avec n suffisant
    assert eligible_milestones(10, weeks_elapsed=10.0) == []  # pas assez de signaux
    assert eligible_milestones(35, weeks_elapsed=10.0) == [30]
    assert eligible_milestones(60, weeks_elapsed=10.0) == [30, 40, 53]


def test_eligible_milestones_at_exact_boundary():
    assert eligible_milestones(30, weeks_elapsed=MIN_WEEKS_SINCE_T0) == [30]


def test_pending_alerts_only_new_milestones():
    assert pending_alerts([30], [30, 40, 53]) == [40, 53]
    assert pending_alerts([30, 40, 53], [30, 40, 53]) == []
    assert pending_alerts([], []) == []


def test_compute_status_integration():
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    status = compute_status("hypothesis_v3cand", 42, -0.05, "2026-10-10T00:00:00+00:00", now, previously_alerted=[30])
    assert status.n_closed == 42
    assert status.eligible == [30, 40]
    assert status.new_alerts == [40]


def test_format_milestone_message_contains_key_facts():
    status = compute_status("hypothesis2_v3cand", 40, 0.123, "2026-10-10T00:00:00+00:00",
                            datetime(2026, 12, 20, tzinfo=timezone.utc), [])
    message = format_milestone_message(status, 40, mde=0.456)
    assert "hypothesis2_v3cand" in message and "40" in message and "+0.123 R" in message
    assert "0.456 R" in message and "Aucune action automatique" in message


def test_format_milestone_message_handles_missing_mean():
    status = compute_status("hypothesis3_v3cand", 30, None, "2026-10-10T00:00:00+00:00",
                            datetime(2026, 12, 20, tzinfo=timezone.utc), [])
    message = format_milestone_message(status, 30, mde=None)
    assert "n/a" in message


def test_format_verdict_message_contains_status_and_reason():
    status = compute_status("hypothesis4_v3cand", 40, 0.1, "2026-10-10T00:00:00+00:00",
                            datetime(2026, 12, 20, tzinfo=timezone.utc), [])
    verdict = ForwardVerdict("hypothesis4_v3cand", 40, 0.2, 0.05, 0.3, "confirmée", "borne basse corrigée (m=44) > 0")
    message = format_verdict_message(status, verdict)
    assert "confirmée" in message and "borne basse corrigée (m=44) > 0" in message
