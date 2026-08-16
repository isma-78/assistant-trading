"""
Tests unitaires de validator — module critique, 100% de couverture
exigée (même règle que risk_engine.py, demande explicite d'Ismaël P2).
"""

from src.risk_engine import AssetSpec
from src.validator import ValidationRejectionReason, validate_signal

WHITELIST = {"GOLD": AssetSpec(symbol="GOLD", min_units=0.01, pip_value_per_unit=0.86)}


def test_asset_not_whitelisted_rejected():
    result = validate_signal(
        asset="UNKNOWN", entry_price=100.0, stop_price=99.0, current_price=100.0,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.ASSET_NOT_WHITELISTED


def test_market_not_tradeable_rejected():
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=100.0,
        market_status="CLOSED", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.MARKET_NOT_TRADEABLE


def test_price_none_rejected():
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=None,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.PRICE_UNAVAILABLE


def test_price_zero_or_negative_rejected():
    for bad_price in (0.0, -5.0):
        result = validate_signal(
            asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=bad_price,
            market_status="TRADEABLE", whitelist=WHITELIST,
        )
        assert result.approved is False
        assert result.reason == ValidationRejectionReason.PRICE_UNAVAILABLE


def test_zero_stop_distance_rejected_as_stale():
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=100.0, current_price=100.0,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.SIGNAL_STALE


def test_price_within_tolerance_approved():
    # stop_distance = 1.0, tolérance = 0.5 -> écart de 0.4 accepté
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=100.4,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is True


def test_price_at_exact_tolerance_boundary_approved():
    # écart == tolérance (0.5) : limite inclusive, pas encore périmé
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=100.5,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is True


def test_price_just_beyond_tolerance_rejected_as_stale():
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=100.50001,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.SIGNAL_STALE


def test_price_drifted_below_entry_also_checked():
    # Le drift est symétrique (abs), pas seulement en défaveur du sens du trade
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=99.0, current_price=99.4,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.SIGNAL_STALE


def test_short_signal_stop_above_entry_still_validated_correctly():
    # Un short a stop_price > entry_price ; stop_distance reste positif via abs()
    result = validate_signal(
        asset="GOLD", entry_price=100.0, stop_price=101.0, current_price=100.3,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is True


def test_internal_error_is_caught_fail_safe():
    # entry_price non numérique -> TypeError dans abs(entry_price - stop_price),
    # capturé par le wrapper fail-safe plutôt que de se propager.
    result = validate_signal(
        asset="GOLD", entry_price="pas un nombre", stop_price=99.0, current_price=100.0,
        market_status="TRADEABLE", whitelist=WHITELIST,
    )
    assert result.approved is False
    assert result.reason == ValidationRejectionReason.INTERNAL_ERROR
