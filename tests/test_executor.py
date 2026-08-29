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
from src.confidence_scorer import PHASE_A_MIN_TRADES_BACKTEST, check_spread_condition, get_median_spread_ratio
from src.executor import (
    GHOST_TRADE_STATUS,
    ManagementActionType,
    OpenTradeState,
    _check_backtest_confidence_gate,
    _compute_guaranteed_stop_adjustment,
    _is_stationx_source,
    cancel_stale_working_orders,
    check_pending_fills,
    compute_tp_allocations,
    compute_trailing_stop_level,
    decide_entry,
    evaluate_position_management,
    force_close_all_open_trades,
    manage_open_trades,
    open_signal,
    reconcile_ghost_positions,
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


# --- _is_stationx_source (§2.11, incident du 21/08/2026) -----------------

_TELEGRAM_CHANNEL = "-1002481537588"  # id numérique réel du canal Station X, voir CLAUDE.md


def test_is_stationx_source_true_for_configured_channel_and_conventional_literal():
    # signals.source/trades.source stockent la valeur brute du canal
    # Telegram pour Station X en production (config.telegram_channel),
    # jamais la chaîne littérale "stationx" — reconnue aussi car utilisée
    # par convention dans les tests.
    assert _is_stationx_source(_TELEGRAM_CHANNEL, _TELEGRAM_CHANNEL) is True
    assert _is_stationx_source("stationx", _TELEGRAM_CHANNEL) is True


def test_is_stationx_source_false_for_known_hypotheses():
    assert _is_stationx_source("hypothesis", _TELEGRAM_CHANNEL) is False
    assert _is_stationx_source("hypothesis3", _TELEGRAM_CHANNEL) is False
    assert _is_stationx_source("hypothesis2", _TELEGRAM_CHANNEL) is False


def test_is_stationx_source_false_for_unknown_future_hypothesis():
    # Le point exact de l'incident du 21/08/2026 : une source jamais
    # explicitement enregistrée nulle part doit être exclue de Station X
    # par défaut (fail-safe), jamais incluse par oubli — c'est pourquoi
    # la reconnaissance est positive (== canal configuré), jamais "tout
    # ce qui n'est pas une hypothèse connue" (voir docstring du module).
    assert _is_stationx_source("hypothesis4", _TELEGRAM_CHANNEL) is False
    assert _is_stationx_source("un-canal-jamais-vu", _TELEGRAM_CHANNEL) is False


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


# --- evaluate_position_management — Hypothèse #4 (TP fixe, pas de trailing) ---
# Validée par Ismaël le 21/08/2026 (voir docs/HYPOTHESES.md, docs/DECISIONS.md).
# 3e patron de sortie, distinct de Station X (tp1/tp2) et Flux B (trailing
# Donchian perpétuel) : take_profit fixe, clôture 100% en une fois, jamais
# de trailing. state.tp1/tp2 = None ici aussi (comme Flux B) — les deux
# tests ci-dessous vérifient explicitement que le trade ne tombe PAS dans
# le trailing Donchian du Flux B malgré ce point commun.

def _h4_state(**overrides):
    base = dict(
        trade_id=1, deal_id="deal-1", asset="EURUSD", source="hypothesis4", direction="long",
        entry_price=100.0, initial_stop_price=98.0, stop_price=98.0,
        tp1=None, tp2=None, tp1_hit=False, tp2_hit=False, remaining_fraction=1.0,
        take_profit=102.0,
    )
    base.update(overrides)
    return OpenTradeState(**base)


def test_management_h4_take_profit_hit_closes_full_position():
    action = evaluate_position_management(_h4_state(), current_price=102.0, atr=None, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_FULL_TP
    assert action.fraction_to_close == 1.0
    assert action.exit_price == 102.0
    assert action.r_multiple == pytest.approx(1.0)  # (102-100)/(100-98)


def test_management_h4_take_profit_hit_short_direction():
    state = _h4_state(direction="short", entry_price=100.0, initial_stop_price=102.0, stop_price=102.0, take_profit=98.0)
    action = evaluate_position_management(state, current_price=98.0, atr=None, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_FULL_TP
    assert action.r_multiple == pytest.approx(1.0)


def test_management_h4_stop_hit_before_take_profit():
    # Le stop fixe est vérifié EN PREMIER (bloc commun à tous les flux) —
    # touché ici plutôt que le TP, jamais de clôture par "take-profit".
    action = evaluate_position_management(_h4_state(), current_price=98.0, atr=None, risk_engine=make_engine())
    assert action.action == ManagementActionType.CLOSE_FULL_STOP
    assert action.fraction_to_close == 1.0


def test_management_h4_no_condition_met_returns_none_never_trailing():
    # Prix entre stop et TP, avec des candles fournies (qui déclencheraient
    # le trailing Donchian du Flux B si ce trade y tombait à tort, puisque
    # tp1 est aussi None ici) : doit rester NONE, jamais UPDATE_TRAILING_STOP.
    window = [_flux_b_candle(100.0) for _ in range(DONCHIAN_PERIOD)]
    candles = window + [_flux_b_candle(101.0)]
    action = evaluate_position_management(
        _h4_state(), current_price=101.0, atr=None, risk_engine=make_engine(), candles=candles,
    )
    assert action.action == ManagementActionType.NONE
    assert "Hypothèse #4" in action.detail


def test_management_h4_ignores_atr_trailing_even_after_tp1_style_state():
    # Défense en profondeur : même avec atr fourni, un trade H4 (take_profit
    # défini) ne doit jamais atteindre le bloc de trailing ATR (Station X)
    # ni le bloc Donchian (Flux B) — la branche H4 retourne toujours avant.
    action = evaluate_position_management(_h4_state(), current_price=101.0, atr=1.0, risk_engine=make_engine())
    assert action.action == ManagementActionType.NONE


# --- _compute_guaranteed_stop_adjustment -----------------------------------
# Décision du 20/08/2026 (Ismaël, assumée) : élargir le stop au minimum
# garanti plutôt que de rejeter — remplace le comportement du 16/08/2026
# (voir docs/DECISIONS.md pour le raisonnement complet des deux entrées).

def test_guaranteed_stop_not_required():
    client = MagicMock()
    client.get_market_snapshot.return_value = {"dealingRules": {}}
    result = _compute_guaranteed_stop_adjustment(client, "EURUSD", "short", reference_price=1.15, stop_price=1.14)
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
    result = _compute_guaranteed_stop_adjustment(client, "BTCUSD", "short", reference_price=100000.0, stop_price=99500.0)
    assert result.widened is False
    assert result.stop_price == 99500.0
    assert result.stop_distance == 500.0


def test_guaranteed_stop_percentage_insufficient_widens_short():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # entry=100000, min requis=250, + marge de sécurité 1%
    # (GUARANTEED_STOP_SAFETY_MARGIN, 21/08/2026, voir docs/DECISIONS.md)
    # = 252,5 ; stop réel à seulement 50 -> élargi au minimum : short,
    # stop au-dessus de l'entrée -> entry + min_distance.
    result = _compute_guaranteed_stop_adjustment(client, "BTCUSD", "short", reference_price=100000.0, stop_price=99950.0)
    assert result.widened is True
    assert result.stop_price == pytest.approx(100252.5)
    assert result.stop_distance == pytest.approx(252.5)


def test_guaranteed_stop_insufficient_widens_long():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 0.25}}
    }
    # long : stop en-dessous de l'entrée -> entry - min_distance (avec la
    # même marge de sécurité 1% que le cas short ci-dessus).
    result = _compute_guaranteed_stop_adjustment(client, "BTCUSD", "long", reference_price=100000.0, stop_price=99950.0)
    assert result.widened is True
    assert result.stop_price == pytest.approx(99747.5)
    assert result.stop_distance == pytest.approx(252.5)


def test_guaranteed_stop_absolute_unit_sufficient():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "POINTS", "value": 10.0}}
    }
    result = _compute_guaranteed_stop_adjustment(client, "GOLD", "short", reference_price=2000.0, stop_price=2015.0)
    assert result.widened is False
    assert result.stop_distance == 15.0


def test_guaranteed_stop_safety_margin_widens_at_exact_raw_boundary():
    # Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : un ordre
    # calculé exactement au minimum brut lu par nous a été rejeté par le
    # broker (minGuaranteedStopDistance dérive en direct). Cas limite
    # précis : distance actuelle == minimum BRUT (aurait été jugée
    # suffisante avant ce correctif) mais < minimum avec la marge de 1%
    # (GUARANTEED_STOP_SAFETY_MARGIN) -> doit désormais élargir.
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 1.0}}
    }
    # entry=1000, min brut = 1000*1% = 10 ; stop à distance exactement 10.
    result = _compute_guaranteed_stop_adjustment(client, "GOLD", "short", reference_price=1000.0, stop_price=1010.0)
    assert result.widened is True
    assert result.stop_distance == pytest.approx(10.1)  # 10 * GUARANTEED_STOP_SAFETY_MARGIN
    assert result.stop_price == pytest.approx(1010.1)


def test_guaranteed_stop_safety_margin_applied_to_absolute_unit_too():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "POINTS", "value": 10.0}}
    }
    # distance actuelle = 10 (== minimum brut, insuffisant avec la marge de 1%).
    result = _compute_guaranteed_stop_adjustment(client, "GOLD", "long", reference_price=2000.0, stop_price=1990.0)
    assert result.widened is True
    assert result.stop_distance == pytest.approx(10.1)
    assert result.stop_price == pytest.approx(1989.9)


def test_guaranteed_stop_adjustment_unknown_direction_raises():
    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 5.0}}
    }
    with pytest.raises(ValueError):
        _compute_guaranteed_stop_adjustment(client, "GOLD", "sideways", reference_price=100.0, stop_price=101.0)


# --- orchestration (DB réelle temporaire + CapitalClient simulé) ---------

def _insert_signal(
    db_path, actif="GOLD", sens="short", entree=100.0, stop=101.0, tp1=98.0, tp2=96.0, tp3=None,
    take_profit=None, confiance=1.0, statut="a_valider", source="station_x", telegram_msg_id=1,
):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (?, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'signal')",
            (telegram_msg_id,),
        ).lastrowid
        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
            "tp1, tp2, tp3, take_profit, confiance, statut, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-08-16T00:00:00Z')",
            (raw_id, source, actif, sens, entree, entree, stop, tp1, tp2, tp3, take_profit, confiance, statut),
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


def test_open_signal_trade_row_visible_to_guard_before_broker_call_resolves(tmp_path):
    # Correctif du 25/08/2026 (voir docs/DECISIONS.md — bug réel trouvé
    # en investiguant l'écart trades réels/backtest, 4 positions
    # H3/ETHUSD simultanées le 21/08/2026) : la ligne `trades` doit
    # exister AVANT l'appel réseau, pas après — sinon le garde-fou
    # `_has_active_signal_or_trade` ne voit rien pendant l'appel et un
    # nouveau signal peut se générer sur le même actif. Simule la
    # fenêtre de course : `place_limit_order` interroge la DB depuis
    # SON PROPRE side_effect, comme le ferait un cycle concurrent.
    from src.technical_strategy_executor import _has_active_signal_or_trade

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0, source="hypothesis3")

    seen_active = {}

    def _place_limit_order(**kwargs):
        seen_active["value"] = _has_active_signal_or_trade(db_path, "GOLD", "hypothesis3")
        return {"deal_id": "deal-xyz", "level": 100.0}

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.side_effect = _place_limit_order

    envelope_manager = CapitalManager(initial_balance=500.0)
    open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert seen_active["value"] is True


def test_open_signal_placement_failure_marks_preinserted_trade_annule(tmp_path):
    # Avant le correctif du 25/08/2026 : un échec de place_limit_order ne
    # laissait AUCUNE ligne trades. Depuis, la ligne pré-insérée (avant
    # l'appel réseau) doit être annulée explicitement — jamais laissée
    # en 'en_attente' sans deal_id (bloquerait indéfiniment le garde-fou
    # sur cet actif, invariant #7).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.side_effect = CapitalApiError("boom")

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result is None
    conn = get_connection(db_path)
    try:
        trades = conn.execute("SELECT * FROM trades").fetchall()
        assert len(trades) == 1
        assert trades[0]["statut"] == "annule"
        assert trades[0]["deal_id"] is None
    finally:
        conn.close()


def test_open_signal_captures_spread_at_signal(tmp_path):
    # §2.6, 24/08/2026 (voir docs/DECISIONS.md) : ferme le gap de
    # market_snapshots.spread jamais alimenté pour le live.
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

    conn = get_connection(db_path)
    try:
        snap = conn.execute("SELECT * FROM market_snapshots WHERE signal_id = ?", (signal_row["id"],)).fetchone()
        assert snap is not None
        assert snap["bid"] == pytest.approx(100.0)
        assert snap["ask"] == pytest.approx(100.2)
        assert snap["spread"] == pytest.approx(0.2)
    finally:
        conn.close()


def test_open_signal_captures_spread_even_when_rejected(tmp_path):
    # Un signal rejeté a quand même un spread réel au moment de
    # l'évaluation — capturé pour l'éligibilité future du couple, même
    # si CE signal ne devient jamais un trade.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=0.1)  # sous le seuil -> rejeté par decide_entry

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result is None
    conn = get_connection(db_path)
    try:
        snap = conn.execute("SELECT * FROM market_snapshots WHERE signal_id = ?", (signal_row["id"],)).fetchone()
        assert snap is not None
        assert snap["spread"] == pytest.approx(0.2)
    finally:
        conn.close()


def test_open_signal_spread_capture_failure_does_not_block_opening(tmp_path):
    # Best-effort (voir docstring de open_signal) : simule un objet
    # connexion qui échoue UNIQUEMENT sur l'INSERT market_snapshots
    # (première fois) et se comporte normalement ensuite — prouve que
    # l'exception est absorbée sans empêcher l'ouverture du trade.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-xyz", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)

    class _FailingConnWrapper:
        def __init__(self, real_conn):
            self._real_conn = real_conn
            self._first_insert_done = False

        def execute(self, query, params=()):
            if "INSERT INTO market_snapshots" in query and not self._first_insert_done:
                self._first_insert_done = True
                raise RuntimeError("panne DB spread")
            return self._real_conn.execute(query, params)

        def __getattr__(self, name):
            return getattr(self._real_conn, name)

    from contextlib import contextmanager

    from src.db import connection_scope as real_connection_scope

    @contextmanager
    def _flaky_connection_scope(path):
        with real_connection_scope(path) as conn:
            yield _FailingConnWrapper(conn)

    with patch("src.executor.connection_scope", _flaky_connection_scope):
        result = open_signal(
            db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
            confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
        )

    assert result == "deal-xyz"  # l'ouverture a quand meme reussi malgre l'echec de capture du spread
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM market_snapshots").fetchone()["n"] == 0
        trade = conn.execute("SELECT * FROM trades").fetchone()
        assert trade["deal_id"] == "deal-xyz"
    finally:
        conn.close()


def test_open_signal_spread_capture_unblocks_confidence_scorer_eligibility(tmp_path):
    # Bout en bout demandé explicitement (24/08/2026) : une fois le
    # spread réellement capturé par open_signal, la condition
    # d'éligibilité spread de confidence_scorer.py doit se comporter
    # normalement (accepter un spread étroit, rejeter un spread large)
    # pour une source LIVE — jusqu'ici toujours indisponible (gap
    # documenté depuis le 20/08/2026).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0, stop=101.0)  # entree=100, stop=101 -> distance 1.0

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.05, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-xyz", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )
    # Simule la clôture du trade (hors périmètre d'open_signal lui-même) —
    # seul le point testé ici est la lecture confidence_scorer une fois
    # le spread réellement capturé.
    with connection_scope(db_path) as conn:
        conn.execute(
            "UPDATE trades SET statut = 'ferme', prix_entree_reel = 100.0, stop_loss_initial = 101.0 "
            "WHERE signal_id = ?", (signal_row["id"],),
        )

    ratio = get_median_spread_ratio(db_path, "GOLD", "station_x")
    assert ratio == pytest.approx(0.05)  # spread 0.05 / distance de stop 1.0
    satisfied, _ = check_spread_condition(ratio)
    assert satisfied is True  # 5% < seuil 15%


def test_open_signal_records_regime_type_and_exit_type_per_source(tmp_path):
    # Ajout 23/08/2026 (voir docs/DECISIONS.md, sortie H2/H3 basculée) :
    # regime_type et exit_type sont deux dimensions INDÉPENDANTES,
    # renseignées à l'ouverture par open_signal() selon la source —
    # jamais fusionnées, jamais devinées pour une source inconnue.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-abc", "level": 100.0}
    envelope_manager = CapitalManager(initial_balance=500.0)

    cases = [
        ("hypothesis", "ma200", "trailing_pur"),          # H1 : inchangée
        ("hypothesis2", "structural_bos_choch", "tp_partiel"),  # H2 : bascule des deux
        ("hypothesis3", "ma200", "tp_partiel"),            # H3 : régime inchangé, sortie basculée
        ("hypothesis4", "ma200", "tp_fixe"),               # H4 : ni l'un ni l'autre ne change
        ("station_x", None, "tp_partiel"),                 # Station X : pas de régime, tp_partiel d'origine
    ]
    for i, (source, expected_regime, expected_exit) in enumerate(cases):
        signal_row = _insert_signal(db_path, source=source, telegram_msg_id=100 + i)
        open_signal(
            db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
            confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
        )
        conn = get_connection(db_path)
        try:
            trade = conn.execute(
                "SELECT regime_type, exit_type FROM trades WHERE signal_id = ?", (signal_row["id"],)
            ).fetchone()
        finally:
            conn.close()
        assert trade["regime_type"] == expected_regime, source
        assert trade["exit_type"] == expected_exit, source


def test_open_signal_no_widening_needed_sizing_unchanged_from_original_stop(tmp_path):
    # Épingle le comportement de sizing quand aucun élargissement n'est
    # nécessaire (dealingRules absent -> guaranteed_required=False,
    # adjustment.stop_price == stop d'origine) — corrigé le 21/08/2026
    # (voir docs/DECISIONS.md) : _compute_guaranteed_stop_adjustment est
    # désormais appelée AVANT decide_entry, ce test vérifie explicitement
    # que ce déplacement ne change RIEN au sizing pour ce cas majoritaire.
    # entree=100, stop=101 -> distance=1 ; enveloppe=500€, risk 2% = 10€ ;
    # units = 10/(1*0.86) = 11.627... -> floor au pas de 0.01 = 11.62.
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
    client.place_limit_order.assert_called_once()
    _, kwargs = client.place_limit_order.call_args
    assert kwargs["size"] == pytest.approx(11.62)
    assert kwargs["guaranteed_stop"] is False
    assert kwargs["stop_distance"] is None

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades").fetchone()
        assert trade["taille_initiale"] == pytest.approx(11.62)
        assert trade["stop_loss_initial"] == pytest.approx(101.0)  # inchangé, pas élargi
        assert trade["stop_elargi"] == 0
        assert trade["stop_origine_signal"] is None
        assert trade["risque_eur"] == pytest.approx(9.99)  # round(11.62 * 1 * 0.86, 2)
        decision = conn.execute(
            "SELECT approved, units, risk_amount_eur FROM risk_decisions WHERE signal_id = ?", (signal_row["id"],)
        ).fetchone()
        assert decision["approved"] == 1
        assert decision["units"] == pytest.approx(11.62)
        # Une seule ligne risk_decisions (pas de second passage) pour un
        # signal qui n'a jamais eu besoin d'élargissement.
        count = conn.execute("SELECT COUNT(*) AS n FROM risk_decisions WHERE signal_id = ?", (signal_row["id"],)).fetchone()["n"]
        assert count == 1
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
    # entree=100, stop=101 -> stop_distance=1, broker exige 5% * 100 = 5,
    # + marge de sécurité 1% (GUARANTEED_STOP_SAFETY_MARGIN, 21/08/2026,
    # voir docs/DECISIONS.md) = 5,05 -> insuffisant, ÉLARGI à 105,05
    # (short : stop au-dessus, entry+min_distance), risk_engine
    # redimensionne SUR ce nouveau stop pour garder le risque à 2% de
    # l'enveloppe (10€) : units = 10/(5,05*0.86) = 2,3026 -> 2,30.
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
    assert call_kwargs["stop_distance"] == pytest.approx(5.05)
    assert call_kwargs["size"] == pytest.approx(2.30)

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE signal_id = ?", (signal_row["id"],)).fetchone()
        assert trade["stop_loss_initial"] == pytest.approx(105.05)
        assert trade["stop_elargi"] == 1
        assert trade["stop_origine_signal"] == 101.0
        assert trade["taille_initiale"] == pytest.approx(2.30)
    finally:
        conn.close()


def test_open_signal_rejected_when_widened_stop_gives_size_below_minimum(tmp_path):
    # Cas limite : le stop élargi est tellement large que la taille
    # calculée dessus tombe sous min_units — l'entrée doit être rejetée
    # (jamais placée avec une taille calculée sur le stop d'origine, ce
    # qui dépasserait le risque budgété). Corrigé le 21/08/2026 (voir
    # docs/DECISIONS.md) : le stop élargi est désormais connu AVANT
    # decide_entry, donc ce rejet arrive en un seul passage, pas un
    # second appel risk_engine après coup.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)  # entree=100, stop=101

    client = MagicMock()
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"},
        # min = 10000 points -> unités calculées bien sous min_units (0.01)
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
        # Une seule ligne risk_decisions désormais (un seul passage) —
        # toujours journalisée, jamais silencieuse.
        decisions = conn.execute(
            "SELECT approved, reason FROM risk_decisions WHERE signal_id = ?", (signal_row["id"],)
        ).fetchall()
        assert len(decisions) == 1
        assert decisions[0]["approved"] == 0
        assert decisions[0]["reason"] == "position_size_below_minimum"
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


def test_check_pending_fills_source_filter_only_stationx(tmp_path):
    # Corrigé le 21/08/2026 (voir docs/DECISIONS.md) : run_executor_loop
    # appelait check_pending_fills SANS aucun filtre — traitait donc
    # aussi les remplissages d'ordres hypothesis3/hypothesis2. Vérifie le
    # nouveau paramètre source_filter (Python), pas seulement `sources`
    # (liste SQL) déjà couvert par le test précédent.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-h3', 'hypothesis3', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-21T00:00:00Z', 'en_attente')",
            (signal_row["id"],),
        )

    client = MagicMock()
    client.get_working_orders.return_value = []
    client.get_open_positions.return_value = [
        {"position": {"dealId": "position-nouveau-id", "workingOrderId": "deal-h3", "level": 99.98}}
    ]

    filled = check_pending_fills(db_path, client, source_filter=lambda s: _is_stationx_source(s, _TELEGRAM_CHANNEL))
    assert filled == 0
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE deal_id = 'deal-h3'").fetchone()
        assert trade["statut"] == "en_attente"  # jamais touché par le filtre stationx
    finally:
        conn.close()


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


# --- reconcile_ghost_positions (28/08/2026, position fantômes) ----------

def _insert_ouvert_trade(db_path, deal_id, source="hypothesis3", actif="US30"):
    signal_row = _insert_signal(db_path, actif=actif, source=source, statut="approuve", telegram_msg_id=hash(deal_id) % 1_000_000)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, ?, ?, ?, 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'ouvert')",
            (signal_row["id"], deal_id, source, actif),
        ).lastrowid
    return trade_id


def test_reconcile_marks_ghost_when_deal_id_absent_from_broker(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_ouvert_trade(db_path, "deal-ghost")

    client = MagicMock()
    client.get_open_positions.return_value = []  # aucune position réelle

    reconciled = reconcile_ghost_positions(db_path, client)

    assert reconciled == 1
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut, r_multiple_total, pnl_net, ferme_at FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == GHOST_TRADE_STATUS
        assert trade["r_multiple_total"] is None  # jamais de prix impute
        assert trade["pnl_net"] is None
        assert trade["ferme_at"] is not None
    finally:
        conn.close()


def test_reconcile_leaves_real_position_untouched(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_ouvert_trade(db_path, "deal-reel")

    client = MagicMock()
    client.get_open_positions.return_value = [{"position": {"dealId": "deal-reel"}}]

    reconciled = reconcile_ghost_positions(db_path, client)

    assert reconciled == 0
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ouvert"
    finally:
        conn.close()


def test_reconcile_ignores_trades_without_deal_id(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, NULL, 'hypothesis3', 'US30', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        )
    client = MagicMock()
    client.get_open_positions.return_value = []

    assert reconcile_ghost_positions(db_path, client) == 0


def test_reconcile_respects_source_filter(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_h3 = _insert_ouvert_trade(db_path, "deal-h3", source="hypothesis3")
    trade_h5 = _insert_ouvert_trade(db_path, "deal-h5", source="hypothesis5")

    client = MagicMock()
    client.get_open_positions.return_value = []  # ni l'un ni l'autre reel sur CE compte

    # Le compte interroge ici est celui de H3 uniquement -> H5 doit rester intact.
    reconciled = reconcile_ghost_positions(db_path, client, source_filter=lambda s: s == "hypothesis3")

    assert reconciled == 1
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT statut FROM trades WHERE id = ?", (trade_h3,)).fetchone()["statut"] == GHOST_TRADE_STATUS
        assert conn.execute("SELECT statut FROM trades WHERE id = ?", (trade_h5,)).fetchone()["statut"] == "ouvert"
    finally:
        conn.close()


def test_reconcile_frees_slot_for_new_signal(tmp_path):
    # Verifie l'effet de bord demande : un trade reconcilie ne bloque
    # plus _has_active_signal_or_trade (technical_strategy_executor).
    from src.technical_strategy_executor import _has_active_signal_or_trade

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    _insert_ouvert_trade(db_path, "deal-ghost", source="hypothesis3", actif="US30")
    assert _has_active_signal_or_trade(db_path, "US30", "hypothesis3") is True

    client = MagicMock()
    client.get_open_positions.return_value = []
    reconcile_ghost_positions(db_path, client)

    assert _has_active_signal_or_trade(db_path, "US30", "hypothesis3") is False


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


def test_manage_open_trades_captures_real_exit_price_from_close_position(tmp_path):
    # 27/08/2026 (voir docs/DECISIONS.md) : trade_partials.prix_sortie_reel/
    # broker_executed_at doivent être alimentés depuis la réponse résolue
    # de client.close_position(), sans écraser prix_sortie (théorique).
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
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE", "updateTime": "2026-08-27T08:59:50.000"},
    }
    client.get_prices.return_value = {"prices": []}
    client.close_position.return_value = {"level": 101.05, "executed_at": "2026-08-27T09:00:00", "confirmation": {}}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "stationx"): envelope_manager}, envelope_ids={("GOLD", "stationx"): envelope_id},
    )

    conn = get_connection(db_path)
    try:
        partial = conn.execute("SELECT * FROM trade_partials WHERE trade_id = ?", (trade_id,)).fetchone()
        assert partial["prix_sortie_reel"] == pytest.approx(101.05)
        assert partial["broker_executed_at"] == "2026-08-27T09:00:00"
        assert partial["prix_sortie"] is not None  # valeur théorique toujours présente, inchangée
        # 28/08/2026 (voir docs/DECISIONS.md, point 2) : moment/prix de la décision persistés.
        assert partial["t_declenchement"] == "2026-08-27T08:59:50.000"
        assert partial["p_declenchement"] == pytest.approx(101.1)  # snapshot.mid = (101.0+101.2)/2
        assert partial["t_demande"] is not None
    finally:
        conn.close()
    # 28/08/2026 (voir docs/DECISIONS.md) : second discriminant transmis.
    assert client.close_position.call_args.kwargs["requested_at"] is not None


def test_manage_open_trades_persists_causal_decomposition_on_full_close(tmp_path):
    # 27/08/2026 (voir docs/DECISIONS.md) : une clôture complète doit
    # déclencher l'attribution causale (best-effort, même patron que
    # analyze_closed_trade) dès que prix_entree_prevu est connu.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-xyz', 'station_x', 'GOLD', 'demo', 'short', 0.01, 101.0, 101.0, 102.0, 102.0, 10.0, 2.0, "
            "'2026-08-16T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 102.0, "offer": 102.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}  # stop touché (short, prix monte)
    client.close_position.return_value = {"level": 102.1, "executed_at": "2026-08-27T09:00:00", "confirmation": {}}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "stationx"): envelope_manager}, envelope_ids={("GOLD", "stationx"): envelope_id},
    )

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM trade_causal_decomposition WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row is not None
        assert row["cout_entree"] == pytest.approx(0.0)  # remplissage exact au prix prévu
        assert row["cout_sortie"] == pytest.approx(0.1)  # sortie réelle défavorable (short, rachat à 102.1 > 102.0 théorique)
    finally:
        conn.close()


def test_manage_open_trades_closes_full_tp_h4_and_updates_envelope(tmp_path):
    # Bout en bout Hypothèse #4 : take_profit chargé depuis signals.take_profit
    # (jamais tp1/tp2, restés NULL) -> _load_open_trade_state -> dispatch
    # CLOSE_FULL_TP -> palier "tp", cloture_reason "take_profit_fixe".
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(
        db_path, actif="EURUSD", sens="long", entree=100.0, stop=98.0,
        tp1=None, tp2=None, take_profit=102.0, source="hypothesis4",
    )

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-h4', 'hypothesis4', 'EURUSD', 'demo', 'long', 0.01, 100.0, 98.0, 98.0, 10.0, 2.0, "
            "'2026-08-21T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 102.0, "offer": 102.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis4")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("EURUSD", "hypothesis4"): envelope_manager}, envelope_ids={("EURUSD", "hypothesis4"): envelope_id},
    )

    client.close_position.assert_called_once()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ferme"
        assert trade["r_multiple_total"] == pytest.approx(1.0)
        assert trade["pnl_net"] == pytest.approx(10.0)
        assert trade["cloture_reason"] == "take_profit_fixe"
        partial = conn.execute("SELECT palier FROM trade_partials WHERE trade_id = ?", (trade_id,)).fetchone()
        assert partial["palier"] == "tp"
    finally:
        conn.close()
    assert envelope_manager.balance == 505.0  # gain : règle des 50% (§2.3) -> moitié enveloppe, moitié réserve


def test_manage_open_trades_reconciles_when_broker_already_closed_position(tmp_path):
    # Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : un stop
    # garanti s'exécute instantanément côté broker ; si notre boucle
    # détecte le même stop touché ensuite, close_position() échoue en 404
    # "position introuvable" — la base doit quand même être réconciliée
    # (5 trades fantômes trouvés en production avant ce correctif), pas
    # rester bloquée indéfiniment.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-ghost', 'hypothesis3', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-21T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}
    client.close_position.side_effect = CapitalApiError("404 not-found.dealId")
    client.get_open_positions.return_value = []  # broker : la position n'existe vraiment plus

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis3")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "hypothesis3"): envelope_manager}, envelope_ids={("GOLD", "hypothesis3"): envelope_id},
    )

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut, r_multiple_total FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ferme"  # réconcilié, jamais resté fantôme
        assert trade["r_multiple_total"] == pytest.approx(-1.0)
    finally:
        conn.close()
    assert envelope_manager.balance == 490.0  # perte bien imputée malgré le 404


def test_manage_open_trades_reraises_when_position_genuinely_still_open(tmp_path):
    # Contre-test : si close_position() échoue mais que la position existe
    # TOUJOURS côté broker (une vraie erreur, pas une clôture manquée),
    # ne jamais la traiter comme fermée — l'exception doit remonter et le
    # trade rester "ouvert" (comportement fail-safe inchangé).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-real-error', 'hypothesis3', 'GOLD', 'demo', 'short', 0.01, 100.0, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-21T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}
    client.close_position.side_effect = CapitalApiError("500 server error")
    client.get_open_positions.return_value = [{"position": {"dealId": "deal-real-error"}}]  # existe toujours

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis3")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "hypothesis3"): envelope_manager}, envelope_ids={("GOLD", "hypothesis3"): envelope_id},
    )

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["statut"] == "ouvert"  # jamais réconcilié sur une erreur réelle
    finally:
        conn.close()
    assert envelope_manager.balance == 500.0  # enveloppe jamais touchée


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

    # Hypothèse #4 : court-circuite la comparaison state.stop_price —
    # même si stop_price == initial_stop_price (comme ici), le code de
    # raison doit rester "take_profit_fixe", jamais "stop_initial".
    tp_action = ManagementAction(action=ManagementActionType.CLOSE_FULL_TP, detail="Take-profit fixe touché (Hypothèse #4)")
    assert _infer_close_reason(tp_action, stop_initial_state) == "take_profit_fixe"


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


def test_manage_open_trades_hypothesis5_routes_to_own_envelope_and_m15_resolution(tmp_path):
    # Hypothèse #5 (§2.11, docs/HYPOTHESES.md, 23/08/2026) : sortie
    # Station X (TP1/TP2 déjà touchés, reliquat 20% sous trailing ATR)
    # sur une source "hypothesis5" — vérifie que _KNOWN_HYPOTHESIS_SOURCES
    # (donc _envelope_source_key) reconnaît bien cette source. Régression
    # ciblée : sans cet ajout, le trade retomberait sur l'enveloppe
    # "stationx" (absente du dict fourni ici), la mise à jour du trailing
    # échouerait silencieusement (KeyError avalé par le fail-safe de
    # manage_open_trades) et update_position_stop ne serait jamais appelé.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(
        db_path, actif="EURUSD", sens="long", entree=1.10, stop=1.09,
        tp1=1.11, tp2=1.12, source="hypothesis5",
    )

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-h5', 'hypothesis5', 'EURUSD', 'demo', 'long', 100.0, "
            "1.10, 1.09, 1.10, 10.0, 2.0, '2026-08-23T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid
        conn.execute(
            "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, executed_at) "
            "VALUES (?, 'tp1', 0.5, 1.11, 1.0, '2026-08-23T01:00:00Z')", (trade_id,),
        )
        conn.execute(
            "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, executed_at) "
            "VALUES (?, 'tp2', 0.3, 1.12, 2.0, '2026-08-23T02:00:00Z')", (trade_id,),
        )

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 1.20, "offer": 1.20, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {
        "prices": [
            {
                "snapshotTimeUTC": "t",
                "openPrice": {"bid": 1.10, "ask": 1.10}, "highPrice": {"bid": 1.13, "ask": 1.13},
                "lowPrice": {"bid": 1.09, "ask": 1.09}, "closePrice": {"bid": 1.12, "ask": 1.12},
            }
            for _ in range(DONCHIAN_PERIOD + 1)
        ]
    }

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis5")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("EURUSD", "hypothesis5"): envelope_manager},
        envelope_ids={("EURUSD", "hypothesis5"): envelope_id},
        include_sources=["hypothesis5"],
    )

    # Résolution M15 depuis la couche session/multi-timeframe du
    # 23/08/2026 après-midi (voir docs/DECISIONS.md) — alignée avec la
    # résolution d'entrée pour ne pas mélanger deux échelles de temps
    # entre décision d'entrée et gestion de la même position.
    client.get_prices.assert_called_once_with("EURUSD", resolution="MINUTE_15", max_bars=DONCHIAN_PERIOD + 1)
    client.update_position_stop.assert_called_once()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["stop_loss_courant"] > 1.10  # resserré au-delà du breakeven, jamais élargi
    finally:
        conn.close()


def test_manage_open_trades_trailing_capped_at_broker_minimum(tmp_path):
    # Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : le canal
    # Donchian voulait resserrer bien plus que le minimum garanti du
    # broker (mesuré contre le prix COURANT, pas l'entrée) — avant ce
    # correctif, la mise à jour était tentée telle quelle et rejetée en
    # boucle (error.invalid.stoploss.minvalue), le stop restant bloqué
    # indéfiniment au dernier niveau accepté. Le candidat doit désormais
    # être plafonné au minimum garanti plutôt que rejeté silencieusement.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, actif="EURUSD", sens="short", tp1=None, tp2=None)

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, guaranteed_stop, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-eur', 'hypothesis3', 'EURUSD', 'demo', 'short', 3700.0, "
            "100.0, 1, 105.0, 105.0, 10.0, 2.0, '2026-08-21T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    # Canal Donchian très serré (candidat brut = 99.5, largement sous le
    # minimum garanti calculé sur le prix courant, ~99.0).
    window = [{"high": 99.5, "low": 97.0, "open": 99.0, "close": 99.0} for _ in range(DONCHIAN_PERIOD)]
    window.append({"high": 99.2, "low": 98.8, "open": 99.0, "close": 99.0})
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
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 98.9, "offer": 99.1, "marketStatus": "TRADEABLE"},
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 5.0}},
    }
    client.get_prices.return_value = {"prices": prices}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis3")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("EURUSD", "hypothesis3"): envelope_manager}, envelope_ids={("EURUSD", "hypothesis3"): envelope_id},
    )

    # Plafonné au minimum garanti (prix courant 99.0 + 5%*1.01 = 4,9995)
    # -> 103,9995, PAS le candidat brut du canal (99.5).
    client.update_position_stop.assert_called_once()
    call_args = client.update_position_stop.call_args
    assert call_args.args[0] == "deal-eur"
    assert call_args.args[1] == pytest.approx(103.9995)
    assert call_args.kwargs["guaranteed_stop"] is True

    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["stop_loss_courant"] == pytest.approx(103.9995)
    finally:
        conn.close()


def test_manage_open_trades_trailing_skipped_when_capped_value_not_tighter(tmp_path):
    # Contre-test : si même le plafond (minimum garanti) n'améliore pas le
    # stop déjà en place, aucune mise à jour n'est tentée — jamais un
    # élargissement, invariant #5, revalidé via risk_engine.evaluate_stop_update.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, actif="EURUSD", sens="short", tp1=None, tp2=None)

    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, guaranteed_stop, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'deal-eur2', 'hypothesis3', 'EURUSD', 'demo', 'short', 3700.0, "
            "100.0, 1, 99.2, 99.2, 10.0, 2.0, '2026-08-21T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid

    window = [{"high": 99.1, "low": 97.0, "open": 99.0, "close": 99.0} for _ in range(DONCHIAN_PERIOD)]
    window.append({"high": 99.0, "low": 98.8, "open": 99.0, "close": 99.0})
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
    client.get_market_snapshot.return_value = {
        "snapshot": {"bid": 98.9, "offer": 99.1, "marketStatus": "TRADEABLE"},
        "dealingRules": {"minGuaranteedStopDistance": {"unit": "PERCENTAGE", "value": 5.0}},
    }
    client.get_prices.return_value = {"prices": prices}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis3")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("EURUSD", "hypothesis3"): envelope_manager}, envelope_ids={("EURUSD", "hypothesis3"): envelope_id},
    )

    # Le plafond (~103,9995) serait plus large que le stop déjà en place
    # (99.2) -> aucune mise à jour tentée côté broker, stop inchangé.
    client.update_position_stop.assert_not_called()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert trade["stop_loss_courant"] == pytest.approx(99.2)
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


def test_manage_open_trades_source_filter_only_stationx_ignores_hypothesis3(tmp_path):
    # Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : executor_loop
    # excluait exclude_sources=["hypothesis"] uniquement — "hypothesis3"
    # n'était jamais exclue, executor_loop gérait donc AUSSI les trades
    # de hypothesis3_executor, en double, avec la mauvaise enveloppe.
    # Vérifie que source_filter=_is_stationx_source protège correctement
    # contre une hypothèse non prévue explicitement dans une liste.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    stationx_trade_id = _insert_open_trade(db_path, signal_row["id"], "stationx", "deal-sx")
    h3_trade_id = _insert_open_trade(db_path, signal_row["id"], "hypothesis3", "deal-h3")

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 101.0, "offer": 101.2, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}

    stationx_id, stationx_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    h3_id, h3_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis3")
    manage_open_trades(
        db_path, client, make_engine(),
        envelope_managers={("GOLD", "stationx"): stationx_manager, ("GOLD", "hypothesis3"): h3_manager},
        envelope_ids={("GOLD", "stationx"): stationx_id, ("GOLD", "hypothesis3"): h3_id},
        source_filter=lambda s: _is_stationx_source(s, _TELEGRAM_CHANNEL),
    )

    conn = get_connection(db_path)
    try:
        stationx_trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (stationx_trade_id,)).fetchone()
        h3_trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (h3_trade_id,)).fetchone()
        assert stationx_trade["statut"] == "ferme"    # stop touché, géré
        assert h3_trade["statut"] == "ouvert"          # jamais touché : pas Station X
    finally:
        conn.close()
    assert h3_manager.balance == 500.0  # enveloppe hypothesis3 jamais affectée


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
    # Enveloppe volontairement réduite à 100€ (29/08/2026) pour que le
    # plafond par-enveloppe (10% = 10€) reste sous le plafond fixe par
    # cluster (50€, voir CLUSTER_EXPOSURE_CAP_EUR) et que ce test isole
    # bien le garde-fou par-enveloppe — sinon, GOLD étant un cluster à
    # lui seul, le nouveau garde-fou de cluster (vérifié en premier)
    # interceptait ce scénario avant d'atteindre celui-ci.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, confiance=1.0)  # entree=100, stop=101 -> risque 2% de 100 = 2€
    # Déjà 9€ engagés sur GOLD/station_x : 9 + 2 = 11 > 10% de 100 = 10
    existing_trade_id = _insert_open_trade(db_path, signal_row["id"], "station_x", "deal-existing")
    with connection_scope(db_path) as conn:
        conn.execute("UPDATE trades SET risque_eur = 9.0 WHERE id = ?", (existing_trade_id,))

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}

    envelope_manager = CapitalManager(initial_balance=100.0)
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


def test_open_signal_rejected_when_cluster_exposure_cap_exceeded_across_sources(tmp_path):
    # 29/08/2026 (voir docs/DECISIONS.md, points 3/11) : le plafond par
    # cluster agrège TOUTES les sources — cas que le plafond par-enveloppe
    # (scopé à une seule source) ne peut PAS détecter. GOLD est un
    # cluster à lui seul (CORRELATION_CLUSTERS) : 4 sources différentes
    # à 10€ chacune (40€) + le risque provisoire boosté (500*4%=20€) sur
    # une 5e source dépasse 50€, alors qu'aucune source individuelle
    # n'approche son propre plafond de 50€.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, source="station_x")
    for source in ("hypothesis2", "hypothesis3", "hypothesis4", "hypothesis5"):
        _insert_open_trade(db_path, signal_row["id"], source, f"deal-{source}")

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
        assert decision["reason"] == "cluster_exposure_cap"
        assert "cluster" in decision["detail"]
    finally:
        conn.close()


def test_open_signal_within_cluster_exposure_cap_still_approved(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, source="station_x")
    _insert_open_trade(db_path, signal_row["id"], "hypothesis2", "deal-h2")  # 10€ seulement dans le cluster

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-new", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result == "deal-new"


def test_open_signal_asset_without_cluster_mapping_skips_cluster_check(tmp_path):
    # Robustesse : un actif hypothétique absent de CORRELATION_CLUSTERS
    # ne doit jamais faire planter open_signal, seulement sauter le
    # garde-fou de cluster (branche `cluster is None`).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, actif="INCONNU_XYZ", source="station_x")

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-new", "level": 100.0}

    custom_whitelist = {"INCONNU_XYZ": AssetSpec(symbol="INCONNU_XYZ", min_units=0.01, pip_value_per_unit=0.86)}
    custom_engine = RiskEngine(
        caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=500.0),
        whitelist=custom_whitelist,
    )
    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, custom_engine, custom_whitelist, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )

    assert result == "deal-new"


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


# --- _check_backtest_confidence_gate / open_signal (Option B, 24/08/2026) -

def _insert_closed_backtest_trades(db_path, actif, source, n, r_multiple, spread=0.1, stop_distance=3.0):
    # Étalé sur plusieurs mois calendaires (27/08/2026, voir
    # docs/DECISIONS.md) — nécessaire pour exercer le bootstrap par blocs
    # calendaires du garde-fou RÉEL (>= 2 blocs distincts requis) ; sans
    # objet pour les autres tests de ce fichier, qui ne regardent jamais
    # la répartition mensuelle.
    for i in range(n):
        signal_row = _insert_signal(db_path, actif=actif, source=source, telegram_msg_id=1000 + i)
        month = (i % 6) + 1
        day = (i % 27) + 1
        with connection_scope(db_path) as conn:
            conn.execute(
                "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
                "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
                "pourcentage_risque_applique, ouvert_at, ferme_at, r_multiple_total, statut) "
                "VALUES (?, ?, ?, 'demo', 'long', 0.01, 100.0, ?, 100.0, 10.0, 2.0, "
                "'2026-01-01T00:00:00Z', ?, ?, 'ferme')",
                (signal_row["id"], source, actif, 100.0 - stop_distance,
                 f"2026-{month:02d}-{day:02d}T00:00:00Z", r_multiple),
            )
            conn.execute(
                "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) "
                "VALUES (?, 1.0, 1.0, ?, '2026-08-01T00:00:00Z')",
                (signal_row["id"], spread),
            )


def test_check_backtest_confidence_gate_noop_for_stationx(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    assert _check_backtest_confidence_gate(db_path, "GOLD", "stationx", 2.0, environment="live") is None
    assert _check_backtest_confidence_gate(db_path, "GOLD", "station_x", 2.0, environment="live") is None


def test_check_backtest_confidence_gate_noop_when_not_eligible(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    # Pas assez de trades backtest (< PHASE_A_MIN_TRADES_BACKTEST) -> jamais éligible.
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", 10, r_multiple=-0.5)
    assert _check_backtest_confidence_gate(db_path, "GOLD", "hypothesis5", 2.0, environment="live") is None


def test_check_backtest_confidence_gate_noop_when_expectancy_positive(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=0.5)
    assert _check_backtest_confidence_gate(db_path, "GOLD", "hypothesis5", 2.0, environment="live") is None


def test_check_backtest_confidence_gate_blocks_when_lower_bound_negative(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    detail = _check_backtest_confidence_gate(db_path, "GOLD", "hypothesis5", 2.0, environment="live")
    assert detail is not None
    assert "hypothesis5_backtest" in detail
    assert "borne basse" in detail.lower()


def test_check_backtest_confidence_gate_noop_in_demo_even_with_negative_expectancy(tmp_path):
    # 27/08/2026 (voir docs/DECISIONS.md) : la démo ne doit JAMAIS être
    # bloquée par ce garde-fou, quelle que soit l'espérance du backtest —
    # c'est désormais un critère de promotion au réel uniquement.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    assert _check_backtest_confidence_gate(db_path, "GOLD", "hypothesis5", 2.0, environment="demo") is None
    assert _check_backtest_confidence_gate(db_path, "GOLD", "hypothesis5", 2.0) is None  # défaut = demo


def test_open_signal_rejected_by_backtest_confidence_gate(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    signal_row = _insert_signal(db_path, source="hypothesis5", confiance=1.0)

    client = MagicMock()
    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
        environment="live",
    )

    assert result is None
    client.get_market_snapshot.assert_not_called()  # court-circuité avant tout appel broker
    conn = get_connection(db_path)
    try:
        decision = conn.execute(
            "SELECT * FROM risk_decisions WHERE signal_id = ?", (signal_row["id"],)
        ).fetchone()
        assert decision["reason"] == "backtest_confidence_gate"
        assert conn.execute("SELECT COUNT(*) AS n FROM trades WHERE source = 'hypothesis5'").fetchone()["n"] == 0
    finally:
        conn.close()


def test_open_signal_not_blocked_in_demo_even_with_negative_backtest_data(tmp_path):
    # 27/08/2026 (voir docs/DECISIONS.md) : le comportement historique
    # (bloquant dès qu'un backtest existait) ne s'applique plus qu'au
    # réel — la démo doit trader sans blocage sur toute sa liste blanche.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    signal_row = _insert_signal(db_path, source="hypothesis5", confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-new", "level": 100.0}
    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
        # environment omis -> défaut "demo"
    )

    assert result == "deal-new"


def test_open_signal_backtest_gate_rejection_sends_notification(tmp_path):
    # 24/08/2026, demande explicite d'Ismaël (voir docs/DECISIONS.md) :
    # un rejet par ce garde-fou doit être notifié, contrairement au
    # comportement initial (silencieux, seulement risk_decisions/logs).
    # 27/08/2026 : ne s'applique plus qu'au réel, environment="live" ici.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    signal_row = _insert_signal(db_path, source="hypothesis5", confiance=1.0)

    client = MagicMock()
    envelope_manager = CapitalManager(initial_balance=500.0)
    with patch("src.executor.send_notification") as mock_notify:
        result = open_signal(
            db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
            confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
            bot_token="tok", chat_id="42", environment="live",
        )

    assert result is None
    mock_notify.assert_called_once()
    args, _ = mock_notify.call_args
    assert args[0] == "tok" and args[1] == "42"
    assert "GOLD" in args[2]
    assert "garde-fou backtest" in args[2]


def test_open_signal_backtest_gate_rejection_no_notification_without_tokens(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=-0.5)
    signal_row = _insert_signal(db_path, source="hypothesis5", confiance=1.0)

    client = MagicMock()
    envelope_manager = CapitalManager(initial_balance=500.0)
    with patch("src.executor.send_notification") as mock_notify:
        open_signal(
            db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
            confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
            environment="live",
        )
    mock_notify.assert_not_called()


def test_open_signal_not_blocked_when_backtest_data_insufficient(tmp_path):
    # Aucune donnée backtest pour ce couple -> le garde-fou ne fait rien,
    # comportement inchangé (approuvé normalement).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, source="hypothesis5", confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-new", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )
    assert result == "deal-new"


def test_open_signal_not_blocked_when_backtest_expectancy_positive(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis5_backtest")
    _insert_closed_backtest_trades(db_path, "GOLD", "hypothesis5_backtest", PHASE_A_MIN_TRADES_BACKTEST, r_multiple=0.5)
    signal_row = _insert_signal(db_path, source="hypothesis5", confiance=1.0)

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}
    client.place_limit_order.return_value = {"deal_id": "deal-new", "level": 100.0}

    envelope_manager = CapitalManager(initial_balance=500.0)
    result = open_signal(
        db_path, client, signal_row, make_engine(), WHITELIST, envelope_manager, envelope_id=1,
        confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
        environment="live",
    )
    assert result == "deal-new"


def test_open_signal_never_gated_for_stationx_even_with_negative_backtest_data(tmp_path):
    # Le garde-fou Option B ne concerne jamais Station X — vérifié même
    # en présence de données backtest négatives sous un nom de source
    # backtest qui, par erreur, ressemblerait à Station X (défense en
    # profondeur : _BACKTEST_SOURCE_BY_LIVE_SOURCE ne contient pas
    # "stationx"/"station_x" comme clé).
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, source="station_x", confiance=1.0)

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


def test_force_close_all_open_trades_source_filter_only_stationx(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path)
    h3_trade_id = _insert_open_trade(db_path, signal_row["id"], "hypothesis3", "deal-h3")

    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": 99.0, "offer": 99.2, "marketStatus": "TRADEABLE"}}

    envelope_id, envelope_manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="hypothesis3")
    closed = force_close_all_open_trades(
        db_path, client,
        envelope_managers={("GOLD", "hypothesis3"): envelope_manager},
        envelope_ids={("GOLD", "hypothesis3"): envelope_id},
        source_filter=lambda s: _is_stationx_source(s, _TELEGRAM_CHANNEL),
    )

    assert closed == 0
    client.close_position.assert_not_called()
    conn = get_connection(db_path)
    try:
        trade = conn.execute("SELECT statut FROM trades WHERE id = ?", (h3_trade_id,)).fetchone()
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
