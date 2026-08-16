"""
Tests d'executor — palier P2. La partie décision/calcul pure
(decide_entry, compute_tp_allocations, evaluate_position_management,
compute_trailing_stop_level) est testée à 100% (demande explicite
d'Ismaël, même règle que risk_engine.py). L'orchestration I/O
(open_signal, manage_open_trades, check_pending_fills,
cancel_stale_working_orders) est testée avec des doubles (DB temporaire
réelle + CapitalClient simulé) mais sans viser la même exhaustivité —
cohérent avec le traitement déjà appliqué à telegram_listener.run_listener.
"""

from unittest.mock import MagicMock

import pytest

from src.capital_manager import CapitalManager
from src.db import connection_scope, get_connection, init_db
from src.envelope_store import load_or_create_envelope
from src.executor import (
    ManagementActionType,
    OpenTradeState,
    _compute_guaranteed_stop_distance,
    cancel_stale_working_orders,
    check_pending_fills,
    compute_tp_allocations,
    compute_trailing_stop_level,
    decide_entry,
    evaluate_position_management,
    manage_open_trades,
    open_signal,
)
from src.go_nogo import GoNoGoStatus
from src.risk_engine import AssetSpec, RiskCaps, RiskEngine, RiskRejectionReason

WHITELIST = {"GOLD": AssetSpec(symbol="GOLD", min_units=0.01, pip_value_per_unit=0.86)}


def make_engine():
    return RiskEngine(
        caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=500.0),
        whitelist=WHITELIST,
    )


# --- decide_entry --------------------------------------------------------

def test_decide_entry_rejected_by_validator_never_reaches_risk_engine():
    decision = decide_entry(
        asset="GOLD", direction="short", entry_price=100.0, stop_price=101.0, confidence=1.0,
        current_price=None,  # -> validator rejette (prix indisponible)
        market_status="TRADEABLE", risk_engine=make_engine(), whitelist=WHITELIST,
        envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
    )
    assert decision.approved is False
    assert decision.risk_decision is None  # jamais atteint


def test_decide_entry_rejected_by_risk_engine():
    decision = decide_entry(
        asset="GOLD", direction="short", entry_price=100.0, stop_price=101.0, confidence=0.5,  # < seuil
        current_price=100.1, market_status="TRADEABLE", risk_engine=make_engine(), whitelist=WHITELIST,
        envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
    )
    assert decision.approved is False
    assert decision.risk_decision.reason == RiskRejectionReason.CONFIDENCE_BELOW_THRESHOLD


def test_decide_entry_approved_end_to_end():
    decision = decide_entry(
        asset="GOLD", direction="short", entry_price=100.0, stop_price=101.0, confidence=1.0,
        current_price=100.1, market_status="TRADEABLE", risk_engine=make_engine(), whitelist=WHITELIST,
        envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
    )
    assert decision.approved is True
    assert decision.risk_decision.units > 0


# --- compute_tp_allocations -----------------------------------------------

def test_compute_tp_allocations_sums_to_total():
    tp1, tp2, tp3 = compute_tp_allocations(1.0, min_units=0.01)
    assert tp1 + tp2 + tp3 == pytest.approx(1.0)
    assert tp1 == pytest.approx(0.5)
    assert tp2 == pytest.approx(0.3)
    assert tp3 == pytest.approx(0.2)


def test_compute_tp_allocations_respects_min_units_rounding():
    tp1, tp2, tp3 = compute_tp_allocations(0.03, min_units=0.01)
    assert tp1 + tp2 + tp3 == pytest.approx(0.03)
    for part in (tp1, tp2, tp3):
        assert round(part / 0.01) == pytest.approx(part / 0.01)


def test_compute_tp_allocations_never_negative_tp3():
    # Cas limite : taille minimale grossière relative au total
    tp1, tp2, tp3 = compute_tp_allocations(0.02, min_units=0.01)
    assert tp3 >= 0
    assert tp1 + tp2 + tp3 == pytest.approx(0.02)


def test_compute_tp_allocations_rounding_overshoot_absorbed_by_tp2():
    # total=1.0, min_units=0.55 : tp1 arrondit à 0.55 (steps=1), tp2 à 0.55
    # (steps=1) -> somme 1.10 > 1.0, tp3 brut négatif (-0.10) -> absorbé
    # par tp2 (0.45), tp3 ramené à 0.0. Déclenche explicitement la
    # branche de sur-allocation de compute_tp_allocations.
    tp1, tp2, tp3 = compute_tp_allocations(1.0, min_units=0.55)
    assert tp1 == pytest.approx(0.55)
    assert tp2 == pytest.approx(0.45)
    assert tp3 == 0.0
    assert tp1 + tp2 + tp3 == pytest.approx(1.0)


# --- compute_trailing_stop_level ------------------------------------------

def test_compute_trailing_stop_level_long_floors_at_breakeven():
    level = compute_trailing_stop_level("long", current_price=101.0, atr=1.0, breakeven=100.5)
    assert level == 100.5  # 101 - 2*1 = 99 < breakeven -> plancher


def test_compute_trailing_stop_level_long_above_breakeven():
    level = compute_trailing_stop_level("long", current_price=110.0, atr=1.0, breakeven=100.5)
    assert level == 108.0  # 110 - 2


def test_compute_trailing_stop_level_short_floors_at_breakeven():
    level = compute_trailing_stop_level("short", current_price=99.0, atr=1.0, breakeven=99.5)
    assert level == 99.5  # 99 + 2 = 101 > breakeven -> plancher


def test_compute_trailing_stop_level_unknown_direction_raises():
    with pytest.raises(ValueError):
        compute_trailing_stop_level("sideways", 100.0, 1.0, 100.0)


# --- evaluate_position_management -----------------------------------------

def _state(**overrides):
    # stop_distance = 1 (entry 100, stop 101) -> tp1 (99) = 1R, tp2 (98) = 2R,
    # arithmétique propre pour les assertions de r_multiple ci-dessous.
    base = dict(
        trade_id=1, deal_id="deal-1", asset="GOLD", direction="short",
        entry_price=100.0, initial_stop_price=101.0, stop_price=101.0,
        tp1=99.0, tp2=98.0, tp1_hit=False, tp2_hit=False, remaining_fraction=1.0,
    )
    base.update(overrides)
    return OpenTradeState(**base)


def test_management_stop_hit_before_anything():
    action = evaluate_position_management(_state(), current_price=101.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_FULL_STOP
    assert action.fraction_to_close == 1.0
    assert action.r_multiple == pytest.approx(-1.0)


def test_management_tp1_hit_moves_stop_to_breakeven():
    action = evaluate_position_management(_state(), current_price=99.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_PARTIAL_TP1
    assert action.fraction_to_close == 0.5
    assert action.new_stop_price == 100.0
    assert action.r_multiple == pytest.approx(1.0)


def test_management_tp2_hit_after_tp1():
    state = _state(tp1_hit=True, stop_price=100.0, remaining_fraction=0.5)
    action = evaluate_position_management(state, current_price=98.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_PARTIAL_TP2
    assert action.fraction_to_close == 0.3
    assert action.r_multiple == pytest.approx(2.0)


def test_management_tp2_not_checked_before_tp1():
    # Prix qui aurait touché TP2 mais TP1 pas encore franchi : TP1 doit
    # être détecté en premier (le prix touche forcément TP1 en route).
    action = evaluate_position_management(_state(), current_price=98.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_PARTIAL_TP1


def test_management_trailing_stop_after_tp1_and_tp2():
    state = _state(tp1_hit=True, tp2_hit=True, stop_price=100.0, remaining_fraction=0.2)
    action = evaluate_position_management(state, current_price=90.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.UPDATE_TRAILING_STOP
    assert action.new_stop_price == 92.0  # 90 + 2*1


def test_management_trailing_stop_never_widens():
    # Stop courant déjà resserré à 90 (short) ; prix à 89 (favorable, le
    # stop à 90 n'est donc pas touché). Le trailing candidat serait
    # 89 + 2*1 = 91, qui ÉLARGIRAIT par rapport à 90 pour un short
    # (invariant #5) -> rejeté par risk_engine.evaluate_stop_update.
    state = _state(tp1_hit=True, tp2_hit=True, stop_price=90.0, remaining_fraction=0.2)
    action = evaluate_position_management(state, current_price=89.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.NONE


def test_management_no_atr_no_trailing_update():
    state = _state(tp1_hit=True, tp2_hit=True, stop_price=100.0, remaining_fraction=0.2)
    action = evaluate_position_management(state, current_price=90.0, atr=None, risk_engine=make_engine())
    assert action.action == ManagementActionType.NONE


def test_management_no_condition_met_returns_none():
    action = evaluate_position_management(_state(), current_price=99.5, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.NONE


def test_management_stop_hit_long_direction():
    # Toutes les autres assertions de gestion utilisent "short" — couvre
    # la branche symétrique "long" de _is_stop_hit/_is_target_hit.
    state = _state(direction="long", entry_price=100.0, initial_stop_price=99.0, stop_price=99.0, tp1=101.0, tp2=102.0)
    action = evaluate_position_management(state, current_price=99.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_FULL_STOP
    assert action.r_multiple == pytest.approx(-1.0)


def test_management_tp1_hit_long_direction():
    state = _state(direction="long", entry_price=100.0, initial_stop_price=99.0, stop_price=99.0, tp1=101.0, tp2=102.0)
    action = evaluate_position_management(state, current_price=101.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_PARTIAL_TP1
    assert action.new_stop_price == 100.0
    assert action.r_multiple == pytest.approx(1.0)


def test_is_target_hit_unknown_direction_raises():
    from src.executor import _is_target_hit
    with pytest.raises(ValueError):
        _is_target_hit("sideways", 100.0, 101.0)


def test_management_internal_error_is_caught_fail_safe():
    action = evaluate_position_management(_state(direction="sideways"), current_price=99.5, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.NONE
    assert "erreur" in action.detail.lower() or "Erreur" in action.detail


# --- _compute_guaranteed_stop_distance ------------------------------------

def test_guaranteed_stop_not_required_returns_zero():
    client = MagicMock()
    client.get_market_snapshot.return_value = {"dealingRules": {}}
    result = _compute_guaranteed_stop_distance(client, "EURUSD", entry_price=1.15, stop_price=1.14)
    assert result == 0.0


def test_guaranteed_stop_percentage_sufficient_distance_uses_signal_stop():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # entry=100000, min = 100000*0.0025 = 250 ; stop réel à 500 -> suffisant
    result = _compute_guaranteed_stop_distance(client, "BTCUSD", entry_price=100000.0, stop_price=99500.0)
    assert result == 500.0


def test_guaranteed_stop_percentage_insufficient_distance_returns_none():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # entry=100000, min requis = 250 ; stop réel à seulement 50 -> insuffisant,
    # jamais élargi silencieusement (augmenterait le risque au-delà du budget)
    result = _compute_guaranteed_stop_distance(client, "BTCUSD", entry_price=100000.0, stop_price=99950.0)
    assert result is None


def test_guaranteed_stop_absolute_unit():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "POINTS", "value": 10.0}}
    }
    result = _compute_guaranteed_stop_distance(client, "GOLD", entry_price=2000.0, stop_price=1985.0)
    assert result == 15.0


# --- orchestration (DB réelle temporaire + CapitalClient simulé) ---------

def _insert_signal(db_path, actif="GOLD", sens="short", entree=100.0, stop=101.0, tp1=98.0, tp2=96.0, tp3=None, confiance=1.0, statut="a_valider"):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'signal')"
        ).lastrowid
        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
            "tp1, tp2, tp3, confiance, statut, created_at) "
            "VALUES (?, 'station_x', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-08-16T00:00:00Z')",
            (raw_id, actif, sens, entree, entree, stop, tp1, tp2, tp3, confiance, statut),
        ).lastrowid
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        return dict(row)


def test_open_signal_rejected_writes_risk_decision_and_no_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=0.0)  # sous le seuil -> rejeté

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result is None
    client.place_limit_order.assert_not_called()
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM risk_decisions").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"] == 0
    finally:
        conn.close()


def test_open_signal_approved_places_limit_order_and_records_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-xyz", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result == "deal-xyz"
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades").fetchone()
        assert trade["deal_id"] == "deal-xyz"
        assert trade["statut"] == "en_attente"
    finally:
        conn.close()


def test_open_signal_rejected_when_stop_too_tight_for_guaranteed_stop(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # entree=100, stop=101 -> stop_distance=1, mais le broker exige un
    # minimum de 5% * 100 = 5 -> insuffisant, doit être rejeté sans
    # jamais élargir silencieusement le stop budgété par risk_engine.
    signal_row = _insert_signal(db_path, confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"},
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 5.0}},
    }

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result is None
    client.place_limit_order.assert_not_called()
    conn = get_connection(db_path)
    try:
        signal = conn.execute("SELECT statut FROM signals WHERE id = ?", (signal_row["id"],)).fetchone()
        assert signal["statut"] == "rejete"
        assert conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"] == 0
    finally:
        conn.close()


def test_open_signal_with_guaranteed_stop_passes_correct_distance(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)  # entree=100, stop=101 -> distance=1

    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"},
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.5}},  # min = 0.5
    }
    client.place_limit_order.return_value = {"deal_id": "deal-xyz", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result == "deal-xyz"
    call_kwargs = client.place_limit_order.call_args.kwargs
    assert call_kwargs["guaranteed_stop"] is True
    assert call_kwargs["stop_distance"] == 1.0


def test_check_pending_fills_transitions_to_ouvert(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'station_x', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'en_attente')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_working_orders.return_value = []  # plus dans les ordres en attente
    client.get_open_positions.return_value = [{"position": {"dealId": "deal-xyz", "level": 99.98}}]

    filled = check_pending_fills(db_path, client)

    assert filled == 1
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ouvert"
        assert trade["prix_entree_reel"] == 99.98
    finally:
        conn.close()


def test_check_pending_fills_still_pending_no_change(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'station_x', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'en_attente')",
            (signal_row["id"],),
        )

    client = MagicMock()
    client.get_working_orders.return_value = [{"workingOrderData": {"dealId": "deal-xyz"}}]
    client.get_open_positions.return_value = []

    filled = check_pending_fills(db_path, client)
    assert filled == 0


def test_cancel_stale_working_orders_cancels_old_ones(tmp_path):
    client = MagicMock()
    client.get_working_orders.return_value = [
        {"workingOrderData": {"dealId": "old-1", "createdDateUTC": "2020-01-01T00:00:00.000"}},
        {"workingOrderData": {"dealId": "recent-1", "createdDateUTC": None}},
    ]
    cancelled = cancel_stale_working_orders("unused.db", client, max_age_seconds=60)
    assert cancelled == 1
    client.cancel_working_order.assert_called_once_with("old-1")


def test_manage_open_trades_closes_full_stop_and_updates_envelope(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'station_x', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}  # pas assez pour un ATR -> None, sans importance ici (stop touché)

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0)
    reserve_total = manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={"GOLD": envelope_manager}, envelope_ids={"GOLD": envelope_id}, reserve_total=0.0,
    )

    client.close_position.assert_called_once()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ferme"
        assert trade["r_multiple_total"] == pytest.approx(-1.0)
        assert trade["pnl_net"] == pytest.approx(-10.0)  # -1R * risque_eur
    finally:
        conn.close()
    assert envelope_manager.balance == 490.0  # perte imputée en totalité (§2.3)
