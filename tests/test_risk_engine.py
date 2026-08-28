"""
Tests unitaires du risk_engine — module critique, 100% de couverture exigée
avant toute exécution même en démo (invariant #2 du projet).
"""

import pytest

from src.risk_engine import (
    AssetSpec,
    ExistingPosition,
    RiskCaps,
    RiskEngine,
    RiskRejectionReason,
    TradeSignal,
    compute_r_multiple,
    compute_weighted_r_multiple,
    evaluate_sizing_plausibility,
)


# --- Fixtures de base ---

def make_caps(**overrides):
    defaults = dict(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=500.0)
    defaults.update(overrides)
    return RiskCaps(**defaults)


def make_whitelist():
    return {
        "EURUSD": AssetSpec(symbol="EURUSD", min_units=1000, pip_value_per_unit=0.0001, weekend_tradable=False),
        "BTCUSD": AssetSpec(symbol="BTCUSD", min_units=0.01, pip_value_per_unit=1.0, weekend_tradable=True),
    }


def make_engine(**caps_overrides):
    return RiskEngine(caps=make_caps(**caps_overrides), whitelist=make_whitelist())


def make_signal(**overrides):
    defaults = dict(
        asset="EURUSD",
        direction="long",
        entry_price=1.1000,
        stop_price=1.0970,
        confidence=0.9,
        boosted=False,
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


# --- RiskCaps ---

def test_riskcaps_valid_construction():
    caps = RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=500.0)
    assert caps.risk_percent_default == 2.0


def test_riskcaps_invalid_percent_raises():
    with pytest.raises(ValueError):
        RiskCaps(risk_percent_default=5.0, risk_percent_boosted=4.0, envelope_initial=500.0)
    with pytest.raises(ValueError):
        RiskCaps(risk_percent_default=0.0, risk_percent_boosted=4.0, envelope_initial=500.0)


def test_riskcaps_invalid_envelope_raises():
    with pytest.raises(ValueError):
        RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=0.0)


# --- AssetSpec ---

def test_assetspec_valid_construction():
    spec = AssetSpec(symbol="EURUSD", min_units=1000, pip_value_per_unit=0.0001)
    assert spec.symbol == "EURUSD"


def test_assetspec_invalid_min_units_raises():
    with pytest.raises(ValueError):
        AssetSpec(symbol="EURUSD", min_units=0, pip_value_per_unit=0.0001)


def test_assetspec_invalid_pip_value_raises():
    with pytest.raises(ValueError):
        AssetSpec(symbol="EURUSD", min_units=1000, pip_value_per_unit=0)


def test_assetspec_size_step_defaults_to_none():
    spec = AssetSpec(symbol="EURUSD", min_units=1000, pip_value_per_unit=0.0001)
    assert spec.size_step is None


def test_assetspec_invalid_size_step_raises():
    with pytest.raises(ValueError):
        AssetSpec(symbol="US30", min_units=0.001, pip_value_per_unit=1.0, size_step=0)


# --- evaluate_sizing_plausibility (28/08/2026, point 3) ---

def test_sizing_plausibility_always_true_without_size_step():
    plausible, detail = evaluate_sizing_plausibility(
        units=0.048, target_risk_eur=10.0, stop_distance=40.0, pip_value_per_unit=0.86, size_step=None,
    )
    assert plausible is True
    assert detail == ""


def test_sizing_plausibility_zero_after_step_rounding_rejected():
    # Reproduit le cas reel US30 (28/08/2026) : 0.048 unites, pas reel 0.1 -> 0.
    plausible, detail = evaluate_sizing_plausibility(
        units=0.048, target_risk_eur=9.92, stop_distance=40.0, pip_value_per_unit=0.8643, size_step=0.1,
    )
    assert plausible is False
    assert "= 0" in detail


def test_sizing_plausibility_within_tolerance_accepted():
    # units=2.314 (position plus grande, le pas de 0.1 pese peu en relatif)
    # -> arrondi a 2.3 : ecart ~0.6%, tres sous le seuil de 20%.
    plausible, detail = evaluate_sizing_plausibility(
        units=2.314, target_risk_eur=10.0, stop_distance=5.0, pip_value_per_unit=0.8643, size_step=0.1,
    )
    assert plausible is True


def test_sizing_plausibility_beyond_tolerance_rejected():
    # 0.187 units, pas 0.1 -> arrondi a 0.1 : ecart ~46%, au-dela du seuil.
    plausible, detail = evaluate_sizing_plausibility(
        units=0.187, target_risk_eur=9.8, stop_distance=40.0, pip_value_per_unit=0.8643, size_step=0.1,
    )
    assert plausible is False
    assert "%" in detail


def test_sizing_plausibility_zero_target_risk_never_divides_by_zero():
    plausible, detail = evaluate_sizing_plausibility(
        units=1.0, target_risk_eur=0.0, stop_distance=1.0, pip_value_per_unit=1.0, size_step=0.1,
    )
    assert plausible is True


# --- evaluate_new_entry : cas nominal ---

def test_entry_approved_long():
    engine = make_engine()
    signal = make_signal(direction="long", entry_price=1.1000, stop_price=1.0970)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is True
    assert decision.units > 0
    assert decision.risk_amount_eur <= 500.0 * 0.02 + 1e-6


def test_entry_approved_short():
    engine = make_engine()
    signal = make_signal(direction="short", entry_price=1.1000, stop_price=1.1030)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is True
    assert decision.units > 0


def test_entry_approved_boosted_uses_higher_cap():
    engine = make_engine()
    signal = make_signal(boosted=True)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is True
    assert decision.risk_amount_eur <= 500.0 * 0.04 + 1e-6


# --- evaluate_new_entry : rejets ---

def test_entry_rejected_go_nogo_locked():
    engine = make_engine()
    signal = make_signal()
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=False
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.GO_NOGO_LOCKED


def test_entry_rejected_confidence_below_threshold():
    engine = make_engine()
    signal = make_signal(confidence=0.5)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.CONFIDENCE_BELOW_THRESHOLD


def test_entry_rejected_asset_not_whitelisted():
    engine = make_engine()
    signal = make_signal(asset="XAUUSD")
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.ASSET_NOT_WHITELISTED


def test_entry_rejected_closed_weekend():
    engine = make_engine()
    signal = make_signal()  # EURUSD, weekend_tradable=False
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True, is_weekend=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.ASSET_CLOSED_WEEKEND


def test_entry_allowed_weekend_when_tradable():
    engine = make_engine()
    signal = make_signal(asset="BTCUSD", direction="long", entry_price=60000, stop_price=59000)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True, is_weekend=True
    )
    assert decision.approved is True


def test_entry_rejected_stop_missing():
    engine = make_engine()
    signal = make_signal(stop_price=None)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.STOP_MISSING


def test_entry_rejected_stop_wrong_side_long():
    engine = make_engine()
    signal = make_signal(direction="long", entry_price=1.1000, stop_price=1.1010)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.STOP_INVALID_SIDE


def test_entry_rejected_stop_wrong_side_short():
    engine = make_engine()
    signal = make_signal(direction="short", entry_price=1.1000, stop_price=1.0990)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.STOP_INVALID_SIDE


def test_entry_rejected_averaging_down():
    engine = make_engine()
    signal = make_signal()
    existing = ExistingPosition(
        asset="EURUSD", direction="long", entry_price=1.1050, stop_price=1.1020, is_losing=True
    )
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
        existing_position=existing,
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.AVERAGING_DOWN


def test_entry_allowed_existing_position_winning():
    engine = make_engine()
    signal = make_signal()
    existing = ExistingPosition(
        asset="EURUSD", direction="long", entry_price=1.0900, stop_price=1.0870, is_losing=False
    )
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
        existing_position=existing,
    )
    assert decision.approved is True


def test_entry_allowed_existing_position_different_asset():
    engine = make_engine()
    signal = make_signal(asset="EURUSD")
    existing = ExistingPosition(
        asset="BTCUSD", direction="long", entry_price=60000, stop_price=59000, is_losing=True
    )
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
        existing_position=existing,
    )
    assert decision.approved is True


def test_entry_rejected_envelope_depleted():
    engine = make_engine()
    signal = make_signal()
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=0.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.ENVELOPE_DEPLETED


def test_entry_rejected_position_size_below_minimum():
    engine = make_engine()
    # Enveloppe minuscule + stop très large => unités calculées < min_units (1000)
    signal = make_signal(entry_price=1.1000, stop_price=0.8000)  # 3000 pips de distance
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=1.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.POSITION_SIZE_BELOW_MINIMUM


def test_entry_rejected_position_size_step_deviation():
    # Reproduit le cas reel US30 (28/08/2026, voir docs/DECISIONS.md) :
    # min_units du code (0.001) beaucoup plus fin que le pas reel du
    # broker (0.1) -> deviation du risque reel au-dela de 20%.
    whitelist = {"US30": AssetSpec(symbol="US30", min_units=0.001, pip_value_per_unit=0.8643, size_step=0.1)}
    engine = RiskEngine(caps=make_caps(), whitelist=whitelist)
    # entree=53713.3, stop=53673.3 (distance=40) -> raw_units ~ (500*0.02)/(40*0.8643) ~ 0.289
    signal = make_signal(asset="US30", direction="long", entry_price=53713.3, stop_price=53673.3)
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.POSITION_SIZE_STEP_DEVIATION


def test_entry_approved_within_size_step_tolerance():
    whitelist = {"US30": AssetSpec(symbol="US30", min_units=0.001, pip_value_per_unit=0.8643, size_step=0.1)}
    engine = RiskEngine(caps=make_caps(), whitelist=whitelist)
    # Stop plus serre -> raw_units plus grand -> arrondi au pas 0.1 reste proche de la cible.
    signal = make_signal(asset="US30", direction="long", entry_price=53713.3, stop_price=53708.3)  # distance=5
    decision = engine.evaluate_new_entry(
        signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True,
    )
    assert decision.approved is True


def test_entry_internal_error_is_caught_fail_safe():
    engine = make_engine()
    # confidence non numérique => TypeError interne à la comparaison, doit être
    # rattrapée et transformée en rejet, jamais laissée remonter (invariant #7).
    bad_signal = TradeSignal(
        asset="EURUSD", direction="long", entry_price=1.1000, stop_price=1.0970,
        confidence="haute",  # type invalide volontaire
    )
    decision = engine.evaluate_new_entry(
        bad_signal, envelope_balance=500.0, confidence_threshold=0.75, go_nogo_ok=True
    )
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.INTERNAL_ERROR


# --- _round_down_to_min (méthode statique testée directement) ---

def test_round_down_to_min_normal_case():
    assert RiskEngine._round_down_to_min(2350, 1000) == 2000


def test_round_down_to_min_non_positive_returns_zero():
    assert RiskEngine._round_down_to_min(0, 1000) == 0.0
    assert RiskEngine._round_down_to_min(-5, 1000) == 0.0


# --- evaluate_stop_update ---

def test_stop_update_long_tightened_approved():
    engine = make_engine()
    decision = engine.evaluate_stop_update(current_stop=1.0970, new_stop=1.0990, direction="long")
    assert decision.approved is True


def test_stop_update_long_widened_rejected():
    engine = make_engine()
    decision = engine.evaluate_stop_update(current_stop=1.0970, new_stop=1.0950, direction="long")
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.STOP_WIDENED


def test_stop_update_short_tightened_approved():
    engine = make_engine()
    decision = engine.evaluate_stop_update(current_stop=1.1030, new_stop=1.1010, direction="short")
    assert decision.approved is True


def test_stop_update_short_widened_rejected():
    engine = make_engine()
    decision = engine.evaluate_stop_update(current_stop=1.1030, new_stop=1.1050, direction="short")
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.STOP_WIDENED


def test_stop_update_unknown_direction_rejected():
    engine = make_engine()
    decision = engine.evaluate_stop_update(current_stop=1.1030, new_stop=1.1010, direction="sideways")
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.STOP_INVALID_SIDE


def test_stop_update_internal_error_is_caught_fail_safe():
    engine = make_engine()
    decision = engine.evaluate_stop_update(current_stop="abc", new_stop=1.1010, direction="long")
    assert decision.approved is False
    assert decision.reason == RiskRejectionReason.INTERNAL_ERROR


# --- compute_r_multiple / compute_weighted_r_multiple (§2.1, §2.10) ---

def test_compute_r_multiple_long_stop_hit_is_minus_one():
    r = compute_r_multiple("long", entry_price=100.0, stop_price=99.0, exit_price=99.0)
    assert r == pytest.approx(-1.0)


def test_compute_r_multiple_long_two_r_target_hit():
    r = compute_r_multiple("long", entry_price=100.0, stop_price=99.0, exit_price=102.0)
    assert r == pytest.approx(2.0)


def test_compute_r_multiple_short_stop_hit_is_minus_one():
    r = compute_r_multiple("short", entry_price=100.0, stop_price=101.0, exit_price=101.0)
    assert r == pytest.approx(-1.0)


def test_compute_r_multiple_short_profit_target():
    r = compute_r_multiple("short", entry_price=100.0, stop_price=101.0, exit_price=97.0)
    assert r == pytest.approx(3.0)


def test_compute_r_multiple_zero_stop_distance_raises():
    with pytest.raises(ValueError):
        compute_r_multiple("long", entry_price=100.0, stop_price=100.0, exit_price=101.0)


def test_compute_r_multiple_unknown_direction_raises():
    with pytest.raises(ValueError):
        compute_r_multiple("sideways", entry_price=100.0, stop_price=99.0, exit_price=101.0)


def test_compute_weighted_r_multiple_tp1_tp2_tp3_split():
    # §2.10 : TP1=50% à +1R, TP2=30% à +2R, TP3=20% à +3R
    r_total = compute_weighted_r_multiple([(0.5, 1.0), (0.3, 2.0), (0.2, 3.0)])
    assert r_total == pytest.approx(0.5 * 1.0 + 0.3 * 2.0 + 0.2 * 3.0)


def test_compute_weighted_r_multiple_single_full_close():
    r_total = compute_weighted_r_multiple([(1.0, -1.0)])
    assert r_total == pytest.approx(-1.0)


def test_compute_weighted_r_multiple_fractions_not_summing_to_one_raises():
    with pytest.raises(ValueError):
        compute_weighted_r_multiple([(0.5, 1.0), (0.3, 2.0)])
