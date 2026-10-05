"""Décision 7 (05/10/2026) : alertes de jalons, aucune action automatique."""

import importlib.util
import os

from src.verdict_counter import VerdictCount

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "milestone_alerts.py")
_spec = importlib.util.spec_from_file_location("milestone_alerts", _PATH)
ma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ma)


def _count(source, n, epoch="E1"):
    return VerdictCount(source, epoch, "2026-09-25", [0.1] * n, 0, 0, 0, 0)


def test_alert_once_per_milestone_with_expectancy():
    alerts = ma.pending_alerts([_count("hypothesis4_v2", 31)], {})
    assert [(k, m) for k, m, _ in alerts] == [("milestone_alert:hypothesis4_v2:E1", 30)]
    assert "+0.100 R" in alerts[0][2] and "Aucune action automatique" in alerts[0][2]
    assert ma.pending_alerts([_count("hypothesis4_v2", 31)], {"milestone_alert:hypothesis4_v2:E1": 30}) == []


def test_new_epoch_restarts_alerting_and_multiple_milestones():
    last = {"milestone_alert:hypothesis2_v2:E1": 53}
    alerts = ma.pending_alerts([_count("hypothesis2_v2", 54, epoch="v3")], last)
    assert [m for _, m, _ in alerts] == [30, 40, 53]


def test_no_alert_below_first_milestone():
    assert ma.pending_alerts([_count("hypothesis_v2", 29)], {}) == []
