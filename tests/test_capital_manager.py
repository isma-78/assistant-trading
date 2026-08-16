"""
Tests unitaires du capital_manager — module critique, 100% de couverture
exigée avant toute exécution même en démo (invariant #2 du projet).
"""

import pytest

from src.capital_manager import CapitalManager, apply_trade_result


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


def test_apply_trade_result_winning_trade_splits_50_50():
    envelope = CapitalManager(initial_balance=500.0)

    reserve_share, reserve_total = apply_trade_result(envelope, pnl=20.0, reserve_total_before=0.0, note="trade gagnant")

    assert envelope.balance == 510.0  # +50% du gain
    assert reserve_share == 10.0
    assert reserve_total == 10.0


def test_apply_trade_result_losing_trade_fully_imputed_to_envelope():
    envelope = CapitalManager(initial_balance=500.0)

    reserve_share, reserve_total = apply_trade_result(envelope, pnl=-30.0, reserve_total_before=100.0, note="trade perdant")

    assert envelope.balance == 470.0
    assert reserve_share == 0.0
    assert reserve_total == 100.0  # jamais touchée par une perte


def test_apply_trade_result_reserve_never_reduced_by_subsequent_loss():
    # Garde-fou explicite du §2.3 : la réserve est "sanctuarisée
    # définitivement, même en cas de série de pertes ultérieure".
    envelope = CapitalManager(initial_balance=500.0)

    _, reserve_after_gain = apply_trade_result(envelope, pnl=40.0, reserve_total_before=0.0, note="gain")
    _, reserve_after_loss = apply_trade_result(envelope, pnl=-1000.0, reserve_total_before=reserve_after_gain, note="grosse perte")

    assert reserve_after_loss == reserve_after_gain


def test_apply_trade_result_zero_pnl_treated_as_no_gain():
    envelope = CapitalManager(initial_balance=500.0)

    reserve_share, reserve_total = apply_trade_result(envelope, pnl=0.0, reserve_total_before=100.0, note="breakeven")

    assert envelope.balance == 500.0
    assert reserve_share == 0.0
    assert reserve_total == 100.0
