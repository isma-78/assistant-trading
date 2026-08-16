"""
Tests unitaires du capital_manager — module critique, 100% de couverture
exigée avant toute exécution même en démo (invariant #2 du projet).
"""

import pytest

from src.capital_manager import CapitalManager


def test_init_valid():
    cm = CapitalManager(initial_balance=500.0)
    assert cm.balance == 500.0
    assert len(cm.history) == 1
    assert cm.history[0].kind == "init"


def test_init_invalid_raises():
    with pytest.raises(ValueError):
        CapitalManager(initial_balance=0.0)
    with pytest.raises(ValueError):
        CapitalManager(initial_balance=-10.0)


def test_apply_trade_pnl_gain():
    cm = CapitalManager(initial_balance=500.0)
    cm.apply_trade_pnl(25.5, note="trade #1")
    assert cm.balance == 525.5
    assert cm.history[-1].kind == "trade_pnl"
    assert cm.history[-1].note == "trade #1"


def test_apply_trade_pnl_loss():
    cm = CapitalManager(initial_balance=500.0)
    cm.apply_trade_pnl(-10.0, note="trade #2")
    assert cm.balance == 490.0


def test_reload_valid():
    cm = CapitalManager(initial_balance=500.0)
    cm.apply_trade_pnl(-500.0, note="épuisement test")
    cm.reload(500.0, note="recharge décidée manuellement après revue")
    assert cm.balance == 500.0
    assert cm.history[-1].kind == "reload"


def test_reload_invalid_amount_raises():
    cm = CapitalManager(initial_balance=500.0)
    with pytest.raises(ValueError):
        cm.reload(0.0, note="justification")
    with pytest.raises(ValueError):
        cm.reload(-50.0, note="justification")


def test_reload_without_note_raises():
    cm = CapitalManager(initial_balance=500.0)
    with pytest.raises(ValueError):
        cm.reload(100.0, note="")


def test_is_depleted_true_and_false():
    cm = CapitalManager(initial_balance=500.0)
    assert cm.is_depleted() is False
    cm.apply_trade_pnl(-500.0, note="épuisement")
    assert cm.is_depleted() is True
