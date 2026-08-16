"""
Tests unitaires du verrou go_nogo — module critique, 100% de couverture
exigée avant toute exécution même en démo (invariant #2 du projet).
"""

from src.go_nogo import evaluate_go_nogo


def test_blocked_when_not_live_environment():
    status = evaluate_go_nogo(
        configured_environment="demo",
        demo_trades_count=30,
        min_demo_trades_required=20,
        risk_engine_tests_passing=True,
    )
    assert status.allowed is False
    assert "live" in status.reason


def test_blocked_when_tests_not_passing():
    status = evaluate_go_nogo(
        configured_environment="live",
        demo_trades_count=30,
        min_demo_trades_required=20,
        risk_engine_tests_passing=False,
    )
    assert status.allowed is False
    assert "test" in status.reason.lower()


def test_blocked_when_insufficient_demo_trades():
    status = evaluate_go_nogo(
        configured_environment="live",
        demo_trades_count=5,
        min_demo_trades_required=20,
        risk_engine_tests_passing=True,
    )
    assert status.allowed is False
    assert "5/20" in status.reason


def test_allowed_when_all_conditions_met():
    status = evaluate_go_nogo(
        configured_environment="live",
        demo_trades_count=20,
        min_demo_trades_required=20,
        risk_engine_tests_passing=True,
    )
    assert status.allowed is True
