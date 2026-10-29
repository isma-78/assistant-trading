from src.entry_filters import (
    MAX_SPREAD_TO_STOP_RATIO, is_limit_level_marketable, spread_filter_rejection, spread_to_stop_ratio,
)
from src.executor import describe_limit_refusal


def test_ratio_basic_and_invalid_inputs():
    assert spread_to_stop_ratio(99.9, 100.0, 100.0, 99.0) == (100.0 - 99.9) / 1.0
    assert spread_to_stop_ratio(None, 100.0, 100.0, 99.0) is None
    assert spread_to_stop_ratio(99.9, None, 100.0, 99.0) is None
    assert spread_to_stop_ratio(100.1, 100.0, 100.0, 99.0) is None  # ask < bid
    assert spread_to_stop_ratio(99.9, 100.0, 100.0, 100.0) is None  # distance nulle


def test_spread_filter_accepts_and_rejects():
    assert spread_filter_rejection(99.98, 100.0, 100.0, 99.0) is None  # 0.02 R
    msg = spread_filter_rejection(99.8, 100.0, 100.0, 99.0)  # 0.2 R
    assert msg is not None and "0.200" in msg
    assert spread_filter_rejection(99.9, 100.0, 100.0, 99.0, max_ratio=0.10) is None  # exactement au seuil
    assert MAX_SPREAD_TO_STOP_RATIO == 0.10


def test_spread_filter_fail_safe_on_missing_data():
    assert "incalculable" in spread_filter_rejection(None, None, 100.0, 99.0)
    assert "incalculable" in spread_filter_rejection(99.9, 100.0, 100.0, 100.0)


def test_marketable_limit_long_and_short():
    assert is_limit_level_marketable("long", 100.0, 99.0, 100.0) is True   # niveau == ask
    assert is_limit_level_marketable("long", 99.5, 99.0, 100.0) is False   # sous l'ask : en attente
    assert is_limit_level_marketable("short", 99.0, 99.0, 100.0) is True   # niveau == bid
    assert is_limit_level_marketable("short", 99.5, 99.0, 100.0) is False
    assert is_limit_level_marketable("short", 98.0, 99.0, 100.0) is True   # cas observé : vente sous le bid


def test_marketable_limit_unknown_inputs_leave_decision_to_broker():
    assert is_limit_level_marketable("long", 100.0, None, 100.0) is False
    assert is_limit_level_marketable("long", 100.0, 99.0, None) is False
    assert is_limit_level_marketable("sideways", 100.0, 99.0, 100.0) is False


def test_marketable_rule_matches_describe_limit_refusal():
    for direction in ("long", "short"):
        for level in (97.0, 99.0, 99.5, 100.0, 101.0):
            expected = describe_limit_refusal(direction, level, 99.0, 100.0)["cote"] == "executable_immediatement"
            assert is_limit_level_marketable(direction, level, 99.0, 100.0) is expected
