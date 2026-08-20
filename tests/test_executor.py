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

from unittest.mock import MagicMock, patch

import pytest

from src import circuit_breaker_store
from src.capital_client import CapitalApiError
from src.capital_manager import CapitalManager
from src.db import connection_scope, get_connection, init_db
from src.envelope_store import load_or_create_envelope
from src.executor import (
    ManagementActionType,
    OpenTradeState,
    _compute_guaranteed_stop_adjustment,
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
from src.market_data import Candle
from src.risk_engine import AssetSpec, RiskCaps, RiskEngine, RiskRejectionReason
from src.trend_strategy import DONCHIAN_PERIOD

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
        trade_id=1, deal_id="deal-1", asset="GOLD", source="stationx", direction="short",
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


# --- evaluate_position_management — Flux B (tp1=None, trailing Donchian) --

def _flux_b_candle(close, high=None, low=None):
    high = high if high is not None else close
    low = low if low is not None else close
    return Candle(time_utc="t", open=close, high=high, low=low, close=close)


def _flux_b_state(**overrides):
    base = dict(
        trade_id=1, deal_id="deal-1", asset="EURUSD", source="hypothesis", direction="long",
        entry_price=100.0, initial_stop_price=95.0, stop_price=95.0,
        tp1=None, tp2=None, tp1_hit=False, tp2_hit=False, remaining_fraction=1.0,
    )
    base.update(overrides)
    return OpenTradeState(**base)


def test_management_flux_b_no_tp_never_reaches_tp_or_atr_branches():
    # tp1=None -> les blocs TP1/TP2/ATR (réservés à Station X) sont tous
    # sautés même à un prix qui les aurait déclenchés avec un TP défini ;
    # sans `candles`, aucune action de trailing n'est possible non plus.
    action = evaluate_position_management(_flux_b_state(), current_price=110.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.NONE


def test_management_flux_b_trailing_tightens_stop_long():
    # Canal plat à 100 (hors bougie courante) -> candidat 100, plus serré
    # que le stop actuel à 95.
    window = [_flux_b_candle(100.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_flux_b_candle(105.0)]
    action = evaluate_position_management(
        _flux_b_state(), current_price=105.0, atr=None, risk_engine=make_engine(), candles=candles,
    )
    assert action.action == ManagementActionType.UPDATE_TRAILING_STOP
    assert action.new_stop_price == 100.0


def test_management_flux_b_trailing_tightens_stop_short():
    window = [_flux_b_candle(100.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_flux_b_candle(95.0)]
    state = _flux_b_state(direction="short", entry_price=100.0, initial_stop_price=105.0, stop_price=105.0)
    action = evaluate_position_management(state, current_price=95.0, atr=None, risk_engine=make_engine(), candles=candles)
    assert action.action == ManagementActionType.UPDATE_TRAILING_STOP
    assert action.new_stop_price == 100.0


def test_management_flux_b_trailing_never_widens():
    # Canal donnerait un stop moins favorable (low=90 < stop actuel 95
    # pour un long) -> rejeté par risk_engine.evaluate_stop_update,
    # aucune action.
    window = [_flux_b_candle(100.0, high=105.0, low=90.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_flux_b_candle(105.0)]
    action = evaluate_position_management(
        _flux_b_state(), current_price=105.0, atr=None, risk_engine=make_engine(), candles=candles,
    )
    assert action.action == ManagementActionType.NONE


def test_management_flux_b_no_candles_no_trailing():
    action = evaluate_position_management(
        _flux_b_state(), current_price=105.0, atr=None, risk_engine=make_engine(), candles=None,
    )
    assert action.action == ManagementActionType.NONE


def test_management_flux_b_stop_hit_takes_priority_over_trailing():
    window = [_flux_b_candle(100.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_flux_b_candle(94.0)]
    action = evaluate_position_management(
        _flux_b_state(), current_price=94.0, atr=None, risk_engine=make_engine(), candles=candles,
    )
    assert action.action == ManagementActionType.CLOSE_FULL_STOP


# --- _compute_guaranteed_stop_adjustment -----------------------------------
# Décision du 20/08/2026 (Ismaël, assumée) : élargir le stop au minimum
# garanti plutôt que de rejeter — remplace le comportement du 16/08/2026
# (voir docs/DECISIONS.md pour le raisonnement complet des deux entrées).

def test_guaranteed_stop_not_required():
    client = MagicMock()
    client.get_market_snapshot.return_value = {"dealingRules": {}}
    result = _compute_guaranteed_stop_adjustment(client, "EURUSD", "short", entry_price=1.15, stop_price=1.14)
    assert result.guaranteed_required is False
    assert result.widened is False
    assert result.stop_distance == 0.0
    assert result.stop_price == 1.14


def test_guaranteed_stop_percentage_sufficient_distance_unchanged():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # entry=100000, min = 100000*0.0025 = 250 ; stop réel à 500 -> déjà suffisant
    result = _compute_guaranteed_stop_adjustment(client, "BTCUSD", "short", entry_price=100000.0, stop_price=99500.0)
    assert result.widened is False
    assert result.stop_price == 99500.0
    assert result.stop_distance == 500.0


def test_guaranteed_stop_percentage_insufficient_widens_short():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # entry=100000, min requis=250 ; stop réel à seulement 50 -> élargi au
    # minimum : short, stop au-dessus de l'entrée -> entry + min_distance.
    result = _compute_guaranteed_stop_adjustment(client, "BTCUSD", "short", entry_price=100000.0, stop_price=99950.0)
    assert result.widened is True
    assert result.stop_price == 100250.0
    assert result.stop_distance == 250.0


def test_guaranteed_stop_insufficient_widens_long():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # long : stop en-dessous de l'entrée -> entry - min_distance.
    result = _compute_guaranteed_stop_adjustment(client, "BTCUSD", "long", entry_price=100000.0, stop_price=99950.0)
    assert result.widened is True
    assert result.stop_price == 99750.0
    assert result.stop_distance == 250.0


def test_guaranteed_stop_absolute_unit_sufficient():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "POINTS", "value": 10.0}}
    }
    result = _compute_guaranteed_stop_adjustment(client, "GOLD", "short", entry_price=2000.0, stop_price=2015.0)
    assert result.widened is False
    assert result.stop_distance == 15.0


def test_guaranteed_stop_adjustment_unknown_direction_raises():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 5.0}}
    }
    with pytest.raises(ValueError):
        _compute_guaranteed_stop_adjustment(client, "GOLD", "sideways", entry_price=100.0, stop_price=101.0)


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


def test_open_signal_records_market_session_on_open(tmp_path):
    # Collecte uniquement (§7.2, 20/08/2026, voir docs/DECISIONS.md et
    # session_marker.py) — vérifie que la session est bien persistée,
    # cohérente avec compute_market_session pour l'heure UTC réelle.
    from datetime import datetime, timezone

    from src.session_marker import compute_market_session

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-xyz", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    expected = compute_market_session(datetime.now(timezone.utc).hour)
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT session_marche FROM trades").fetchone()
        assert trade["session_marche"] == expected
    finally:
        conn.close()


def test_open_signal_records_align_matinale_on_open(tmp_path):
    # §3.8, variable #1 — collecte uniquement (docs/DECISIONS.md, 20/08/2026).
    # Signal par défaut : GOLD short (_insert_signal) -> aligné avec un
    # biais de Matinale "baissier".
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (2, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'matinale')"
        ).lastrowid
        conn.execute(
            "INSERT INTO matinale_summaries "
            "(raw_message_id, raw_asset_mention, actif, biais_corps, sentiment_tag, contradiction_detectee, published_at) "
            "VALUES (?, 'Gold', 'GOLD', 'indetermine', 'baissier', 0, '2026-08-16T00:00:00Z')",
            (raw_id,),
        )

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-xyz", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    conn = get_connection(db_path)
    try:
        trade_id = conn.execute("SELECT id FROM trades").fetchone()["id"]
        features = conn.execute("SELECT align_matinale FROM trade_features WHERE trade_id = ?", (trade_id,)).fetchone()
        assert features["align_matinale"] == 1
    finally:
        conn.close()


def test_open_signal_widens_stop_and_resizes_when_too_tight_for_guaranteed_stop(tmp_path):
    # Décision du 20/08/2026 (Ismaël, assumée — voir docs/DECISIONS.md) :
    # entree=100, stop=101 -> stop_distance=1, broker exige 5% * 100 = 5 ->
    # insuffisant, ÉLARGI à 105 (short : stop au-dessus, entry+min_distance),
    # risk_engine redimensionne SUR ce nouveau stop pour garder le risque
    # à 2% de l'enveloppe (10€) : units = 10/(5*0.86) = 2.3256 -> 2.32.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"},
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 5.0}},
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
    assert call_kwargs["stop_distance"] == 5.0
    assert call_kwargs["size"] == pytest.approx(2.32)

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE signal_id = ?", (signal_row["id"],)).fetchone()
        assert trade["stop_loss_initial"] == 105.0
        assert trade["stop_elargi"] == 1
        assert trade["stop_origine_signal"] == 101.0
        assert trade["taille_initiale"] == pytest.approx(2.32)
    finally:
        conn.close()


def test_open_signal_rejected_when_resize_after_widening_below_minimum_size(tmp_path):
    # Cas limite : le stop élargi est tellement large que la taille
    # redimensionnée tombe sous min_units — l'entrée doit être rejetée à
    # ce stade (jamais placée avec l'ancienne taille calculée sur le
    # stop d'origine, ce qui dépasserait le risque budgété).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)  # entree=100, stop=101

    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"},
        # min = 10000 points -> unités redimensionnées bien sous min_units (0.01)
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "POINTS", "value": 10000.0}},
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
        # Deux lignes risk_decisions : la décision initiale (stop d'origine)
        # puis le rejet du redimensionnement (stop élargi) — les deux
        # journalisées, jamais silencieux.
        assert conn.execute("SELECT COUNT(*) AS n FROM risk_decisions WHERE signal_id = ?", (signal_row["id"],)).fetchone()["n"] == 2
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
    # Bug réel trouvé le 16/08/2026 : la position obtient un NOUVEAU
    # dealId, différent de celui de l'ordre limite d'origine — le seul
    # champ qui relie les deux est position.workingOrderId (voir
    # docs/DECISIONS.md et executor.check_pending_fills).
    client.get_open_positions.return_value = [
        {"position": {"dealId": "position-nouveau-id", "workingOrderId": "deal-xyz", "level": 99.98}}
    ]

    filled = check_pending_fills(db_path, client)

    assert filled == 1
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ouvert"
        # deal_id doit être réécrit avec celui de la POSITION, pas
        # laissé à celui de l'ordre limite d'origine — sinon toute
        # gestion ultérieure (clôture, stop) référencerait un deal_id
        # qui n'existe plus côté broker.
        assert trade["deal_id"] == "position-nouveau-id"
        assert trade["prix_entree_reel"] == 99.98
    finally:
        conn.close()


def test_check_pending_fills_sends_open_notification(tmp_path):
    # §7.2, absent avant le 20/08/2026 (voir docs/DECISIONS.md) : les deux
    # premiers trades réels du Flux B avaient été ouverts sans aucune
    # notification.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'hypothesis', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'en_attente')",
            (signal_row["id"],),
        )

    client = MagicMock()
    client.get_working_orders.return_value = []
    client.get_open_positions.return_value = [
        {"position": {"dealId": "position-nouveau-id", "workingOrderId": "deal-xyz", "level": 99.98}}
    ]

    with patch("src.executor.send_notification") as mock_notify:
        check_pending_fills(db_path, client, bot_token="tok", chat_id="42")

    mock_notify.assert_called_once()
    message = mock_notify.call_args[0][2]
    assert "GOLD" in message
    assert "hypothesis" in message
    assert "99.98" in message
    assert "101.0" in message


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


def test_check_pending_fills_sources_filter_ignores_other_sources(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-stationx', 'stationx', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'en_attente')",
            (signal_row["id"],),
        )

    client = MagicMock()
    client.get_working_orders.return_value = []  # plus en attente
    client.get_open_positions.return_value = [
        {"position": {"dealId": "position-nouveau-id", "workingOrderId": "deal-stationx", "level": 99.98}}
    ]

    # Filtré sur "hypothesis" uniquement : le trade stationx ci-dessus,
    # bien que réellement rempli côté broker, ne doit pas être touché par
    # cet appel (c'est celui de l'autre boucle qui s'en chargera).
    filled = check_pending_fills(db_path, client, sources=["hypothesis"])
    assert filled == 0


def test_cancel_stale_working_orders_cancels_old_ones(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    client = MagicMock()
    client.get_working_orders.return_value = [
        {"workingOrderData": {"dealId": "old-1", "createdDateUTC": "2020-01-01T00:00:00.000"}},
        {"workingOrderData": {"dealId": "recent-1", "createdDateUTC": None}},
    ]
    cancelled = cancel_stale_working_orders(db_path, client, max_age_seconds=60)
    assert cancelled == 1
    client.cancel_working_order.assert_called_once_with("old-1")


def test_cancel_stale_working_orders_marks_trade_annule(tmp_path):
    # Bug réel trouvé le 20/08/2026 (voir docs/DECISIONS.md) : l'ordre
    # était bien annulé côté broker mais trades.statut restait bloqué à
    # "en_attente" indéfiniment — trade fantôme qui bloquait tout nouveau
    # signal Flux B sur l'actif concerné.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, actif="ETHUSD")
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-eth', 'hypothesis', 'ETHUSD', 'demo', 'long', 0.05, 2100.0, 1900.0, 1900.0, 10.0, 2.0, "
            "'2026-08-19T20:21:18Z', 'en_attente')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_working_orders.return_value = [
        {"workingOrderData": {"dealId": "deal-eth", "createdDateUTC": "2020-01-01T00:00:00.000"}},
    ]
    cancelled = cancel_stale_working_orders(db_path, client, max_age_seconds=60)

    assert cancelled == 1
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "annule"
    finally:
        conn.close()


def test_cancel_stale_working_orders_does_not_touch_trade_on_api_failure(tmp_path):
    # Fail-safe (invariant #7) : si l'annulation échoue côté broker, le
    # trade doit rester "en_attente" — jamais marqué annulé sur une base
    # incertaine.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, actif="ETHUSD")
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-eth', 'hypothesis', 'ETHUSD', 'demo', 'long', 0.05, 2100.0, 1900.0, 1900.0, 10.0, 2.0, "
            "'2026-08-19T20:21:18Z', 'en_attente')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_working_orders.return_value = [
        {"workingOrderData": {"dealId": "deal-eth", "createdDateUTC": "2020-01-01T00:00:00.000"}},
    ]
    client.cancel_working_order.side_effect = CapitalApiError("boom")
    cancelled = cancel_stale_working_orders(db_path, client, max_age_seconds=60)

    assert cancelled == 0
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "en_attente"
    finally:
        conn.close()


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

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "stationx"): envelope_manager}, envelope_ids={("GOLD", "stationx"): envelope_id},
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


def test_infer_close_reason_all_branches():
    from src.executor import ManagementAction, _infer_close_reason

    base_state = _state(entry_price=100.0, initial_stop_price=101.0)

    urgence_action = ManagementAction(action=ManagementActionType.CLOSE_FULL_STOP, detail="Arrêt d'urgence (/stop_urgence)")
    assert _infer_close_reason(urgence_action, base_state) == "stop_urgence"

    normal_action = ManagementAction(action=ManagementActionType.CLOSE_FULL_STOP, detail="Stop touché")
    stop_initial_state = _state(entry_price=100.0, initial_stop_price=101.0, stop_price=101.0)
    assert _infer_close_reason(normal_action, stop_initial_state) == "stop_initial"

    breakeven_state = _state(entry_price=100.0, initial_stop_price=101.0, stop_price=100.0)
    assert _infer_close_reason(normal_action, breakeven_state) == "stop_breakeven"

    trailing_state = _state(entry_price=100.0, initial_stop_price=101.0, stop_price=99.5)
    assert _infer_close_reason(normal_action, trailing_state) == "trailing"


def test_weighted_r_multiple_for_trade_combines_all_partials(tmp_path):
    # Bug réel trouvé le 20/08/2026 (voir docs/DECISIONS.md) :
    # trades.r_multiple_total ne stockait que le R du DERNIER palier
    # fermé, jamais le total pondéré sur l'ensemble des paliers malgré
    # son nom — compute_weighted_r_multiple existait, testée, mais
    # jamais appelée depuis executor.py.
    from src.executor import _weighted_r_multiple_for_trade

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'station_x', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 100.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid
        conn.execute(
            "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at) "
            "VALUES (?, 'tp1', 0.5, 98.0, 2.0, 'TP1 touché', '2026-08-16T00:01:00Z')",
            (trade_id,),
        )
        conn.execute(
            "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at) "
            "VALUES (?, 'sl', 0.5, 100.0, 0.0, 'Stop touché', '2026-08-16T00:02:00Z')",
            (trade_id,),
        )

    # Ancien comportement (bugué) : aurait retourné 0.0 (seul le dernier
    # palier). Comportement correct : 0.5*2.0 + 0.5*0.0 = 1.0.
    assert _weighted_r_multiple_for_trade(db_path, trade_id) == pytest.approx(1.0)


def test_manage_open_trades_multi_leg_close_uses_weighted_r_and_notifies(tmp_path):
    # Bout en bout : TP1 touché (clôture partielle notifiée) puis stop au
    # breakeven touché (clôture finale notifiée avec le R total pondéré,
    # pas seulement le R du dernier palier — même bug que le test ci-dessus).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)  # short, entrée=100, stop=101, tp1=98, tp2=96
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'station_x', 'GOLD', 'demo', 'short', 1.0, "
            "100.0, 101.0, 101.0, 10.0, 2.0, '2026-08-16T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_prices.return_value = {"prices": []}
    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    envelopes = {("GOLD", "stationx"): envelope_manager}
    envelope_ids = {("GOLD", "stationx"): envelope_id}

    with patch("src.executor.send_notification") as mock_notify:
        # Cycle 1 : prix à 98 -> TP1 touché (R=+2.0), stop remonté au breakeven (100).
        client.get_market_snapshot.return_value = {"snapshot": {"bid": 98.0, "offer": 98.0, "marketStatus": "TRADEABLE"}}
        manage_open_trades(db_path, client, make_engine(), envelopes, envelope_ids, bot_token="tok", chat_id="42")

        # Cycle 2 : prix à 100 -> stop breakeven touché (R=0.0 sur ce palier).
        client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.0, "marketStatus": "TRADEABLE"}}
        manage_open_trades(db_path, client, make_engine(), envelopes, envelope_ids, bot_token="tok", chat_id="42")

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ferme"
        # 0.5*2.0 (TP1) + 0.5*0.0 (breakeven) = 1.0 — jamais 0.0 (dernier palier seul).
        assert trade["r_multiple_total"] == pytest.approx(1.0)
    finally:
        conn.close()

    assert mock_notify.call_count == 2
    partial_message = mock_notify.call_args_list[0][0][2]
    close_message = mock_notify.call_args_list[1][0][2]
    assert "TP1" in partial_message
    assert "+2.00R" in partial_message
    assert "stop au breakeven" in close_message
    assert "+1.00R" in close_message


def test_manage_open_trades_flux_b_trailing_forwards_guaranteed_stop(tmp_path):
    # Régression du bug réel trouvé en production le 20/08/2026 : les 3
    # positions Flux B alors ouvertes avaient toutes un stop garanti côté
    # broker, et la mise à jour du trailing échouait faute de transmettre
    # ce flag (error.vallidation.guaranteed-stop-loss.required). Voir
    # docs/DECISIONS.md.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, actif="GBPUSD", sens="short", tp1=None, tp2=None)

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, guaranteed_stop, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-gbp', 'hypothesis', 'GBPUSD', 'demo', 'short', 1400.0, "
            "100.0, 1, 105.0, 105.0, 10.0, 2.0, '2026-08-20T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    window = [{"high": 102.0, "low": 98.0, "open": 100.0, "close": 100.0} for _ in range(DONCHIAN_PERIOD)]
    window.append({"high": 99.0, "low": 97.0, "open": 99.0, "close": 99.0})
    prices = [
        {
            "snapshotTimeUTC": "t",
            "openPrice": {"bid": c["open"], "ask": c["open"]},
            "highPrice": {"bid": c["high"], "ask": c["high"]},
            "lowPrice": {"bid": c["low"], "ask": c["low"]},
            "closePrice": {"bid": c["close"], "ask": c["close"]},
        }
        for c in window
    ]

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 98.9, "offer": 99.1, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": prices}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GBPUSD", "demo", 500.0, source="hypothesis")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GBPUSD", "hypothesis"): envelope_manager}, envelope_ids={("GBPUSD", "hypothesis"): envelope_id},
    )

    client.update_position_stop.assert_called_once_with("deal-gbp", 102.0, guaranteed_stop=True)
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["stop_loss_courant"] == 102.0
    finally:
        conn.close()


def _insert_open_trade(db_path, signal_id, source, deal_id):
    with connection_scope(db_path) as conn:
        return conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, ?, ?, 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'ouvert')",
            (signal_id, deal_id, source),
        ).lastrowid


def test_manage_open_trades_exclude_sources_skips_hypothesis(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    hyp_trade_id = _insert_open_trade(db_path, signal_row["id"], "hypothesis", "deal-hyp")

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "hypothesis"): envelope_manager}, envelope_ids={("GOLD", "hypothesis"): envelope_id},
        exclude_sources=["hypothesis"],
    )

    client.close_position.assert_not_called()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (hyp_trade_id,)).fetchone()
        assert trade["statut"] == "ouvert"  # jamais touché : exclu par le filtre
    finally:
        conn.close()


def test_manage_open_trades_include_sources_only_hypothesis(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    stationx_trade_id = _insert_open_trade(db_path, signal_row["id"], "stationx", "deal-sx")
    hyp_trade_id = _insert_open_trade(db_path, signal_row["id"], "hypothesis", "deal-hyp")

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}

    _, stationx_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    hyp_id, hyp_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "stationx"): stationx_manager, ("GOLD", "hypothesis"): hyp_manager},
        envelope_ids={("GOLD", "hypothesis"): hyp_id},
        include_sources=["hypothesis"],
    )

    conn = get_connection(db_path)
    try:
        stationx_trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (stationx_trade_id,)).fetchone()
        hyp_trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (hyp_trade_id,)).fetchone()
        assert stationx_trade["statut"] == "ouvert"  # non inclus, jamais touché
        assert hyp_trade["statut"] == "ferme"        # inclus, fermé (stop touché)
    finally:
        conn.close()
    assert hyp_manager.balance == 490.0
    assert stationx_manager.balance == 500.0  # enveloppe stationx jamais affectée


def test_manage_open_trades_triggers_trade_analysis_on_full_close(tmp_path):
    # Bug réel trouvé le 16/08/2026 pendant le test encadré : trade_analyzer.py
    # était entièrement construit et testé mais jamais appelé depuis
    # executor.py. Ce test vérifie que la clôture complète déclenche bien
    # l'analyse post-trade (voir docs/DECISIONS.md).
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
    client.get_prices.return_value = {"prices": []}

    anthropic_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text="Le trade sur GOLD a touché le stop après une brève ouverture.")]
    anthropic_client.messages.create.return_value = response

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with patch("src.trade_analyzer.send_notification") as mock_notify:
        manage_open_trades(
            db_path, client, make_engine(),
            envelope_managers={("GOLD", "stationx"): envelope_manager}, envelope_ids={("GOLD", "stationx"): envelope_id},
            anthropic_client=anthropic_client, bot_token="tok", chat_id="42",
        )

    conn = get_connection(db_path)
    try:
        analysis = conn.execute("SELECT * FROM trade_analysis WHERE trade_id = ?", (trade_id,)).fetchone()
        assert analysis is not None
        assert analysis["r_multiple_realise"] == pytest.approx(-1.0)
        assert analysis["resume_narratif"] is not None
        assert analysis["source"] == "stationx"
    finally:
        conn.close()
    mock_notify.assert_called_once()


# --- coupe-circuits / exposition simultanée (§2.7, §2.3) -----------------

def test_open_signal_rejected_when_asset_blocked_by_circuit_breaker(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)
    circuit_breaker_store.trigger_manual_pause(db_path, "GOLD", "test")

    client = MagicMock()
    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result is None
    client.get_market_snapshot.assert_not_called()  # court-circuité avant même de consulter le marché
    conn = get_connection(db_path)
    try:
        decision = conn.execute("SELECT * FROM risk_decisions").fetchone()
        assert decision["reason"] == "circuit_breaker_blocked"
        assert conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"] == 0
    finally:
        conn.close()


def test_open_signal_rejected_when_exposure_cap_exceeded(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)  # entree=100, stop=101 -> risque 2% de 500 = 10€
    # Déjà 45€ engagés sur GOLD/stationx : 45 + 10 = 55 > 10% de 500 = 50
    existing_trade_id = _insert_open_trade(db_path, signal_row["id"], "station_x", "deal-existing")
    with connection_scope(db_path) as conn:
        conn.execute("UPDATE trades SET risque_eur = 45.0 WHERE id = ?", (existing_trade_id,))

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
        decision = conn.execute("SELECT * FROM risk_decisions WHERE signal_id = ?", (signal_row["id"],)).fetchone()
        assert "exposition" in decision["detail"]
    finally:
        conn.close()


def test_open_signal_within_exposure_cap_still_approved(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)
    existing_trade_id = _insert_open_trade(db_path, signal_row["id"], "station_x", "deal-existing")
    with connection_scope(db_path) as conn:
        conn.execute("UPDATE trades SET risque_eur = 20.0 WHERE id = ?", (existing_trade_id,))  # 20 + 10 = 30 <= 50

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-new", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result == "deal-new"


# --- force_close_all_open_trades (/stop_urgence, §7.1) --------------------

def test_force_close_all_open_trades_closes_position_and_credits_envelope(tmp_path):
    from src.executor import force_close_all_open_trades

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    trade_id = _insert_open_trade(db_path, signal_row["id"], "stationx", "deal-live")

    client = MagicMock()
    # short, entrée=100, stop initial=101 ; prix courant=99 -> R positif (favorable)
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 99.0, "offer": 99.2, "marketStatus": "TRADEABLE"}}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with patch("src.trade_analyzer.send_notification"), patch("src.executor.send_notification") as mock_notify:
        closed = force_close_all_open_trades(
            db_path, client,
            envelope_managers={("GOLD", "stationx"): envelope_manager},
            envelope_ids={("GOLD", "stationx"): envelope_id},
            bot_token="tok", chat_id="42",
        )

    assert closed == 1
    client.close_position.assert_called_once()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut, cloture_reason FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ferme"
        # §7.1/§7.2, 20/08/2026 (docs/DECISIONS.md) : une clôture forcée
        # par /stop_urgence doit être identifiable comme telle, pas
        # confondue avec une sortie organique (stop/trailing).
        assert trade["cloture_reason"] == "stop_urgence"
    finally:
        conn.close()
    assert envelope_manager.balance > 500.0  # trade gagnant -> enveloppe créditée

    close_message = mock_notify.call_args[0][2]
    assert "arrêt d'urgence" in close_message


def test_force_close_all_open_trades_respects_source_filter(tmp_path):
    from src.executor import force_close_all_open_trades

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    hyp_trade_id = _insert_open_trade(db_path, signal_row["id"], "hypothesis", "deal-hyp")

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 99.0, "offer": 99.2, "marketStatus": "TRADEABLE"}}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis")
    closed = force_close_all_open_trades(
        db_path, client,
        envelope_managers={("GOLD", "hypothesis"): envelope_manager},
        envelope_ids={("GOLD", "hypothesis"): envelope_id},
        exclude_sources=["hypothesis"],
    )

    assert closed == 0
    client.close_position.assert_not_called()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (hyp_trade_id,)).fetchone()
        assert trade["statut"] == "ouvert"
    finally:
        conn.close()


def test_force_close_all_open_trades_cancels_pending_orders(tmp_path):
    from src.executor import force_close_all_open_trades

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'order-pending', 'stationx', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'en_attente')",
            (signal_row["id"],),
        )

    client = MagicMock()
    closed = force_close_all_open_trades(db_path, client, envelope_managers={}, envelope_ids={})

    assert closed == 0
    client.cancel_working_order.assert_called_once_with("order-pending")
