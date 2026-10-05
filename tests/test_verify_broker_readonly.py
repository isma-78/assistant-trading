import importlib.util
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_broker_readonly.py")
_spec = importlib.util.spec_from_file_location("verify_broker_readonly", _PATH)
vbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vbr)

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)


def _pos(deal_id, working_order_id=None, stop=1.0, level=1.2, size=100, created="2026-10-05T06:00:00.000"):
    return {
        "position": {
            "dealId": deal_id, "workingOrderId": working_order_id, "stopLevel": stop, "level": level,
            "size": size, "direction": "BUY", "guaranteedStop": True, "createdDateUTC": created,
        },
        "market": {"epic": "EURUSD"},
    }


def test_position_in_db_with_stop_is_not_critical():
    report = vbr.classify_positions([_pos("P1")], {"P1": (1, "hypothesis2_v2", "EURUSD", "ouvert")}, now=NOW)
    assert report[0]["in_db"] and report[0]["stop_present"] and not report[0]["critical"]
    assert report[0]["age_hours"] == 12.0
    assert report[0]["risk_quote_currency"] == pytest.approx(20.0)


def test_position_matched_by_working_order_id():
    report = vbr.classify_positions([_pos("P2", "O2")], {"O2": (2, "hypothesis4_v2", "EURUSD", "ouvert")}, now=NOW)
    assert report[0]["in_db"] and report[0]["db"][0] == 2


def test_out_of_base_position_is_critical_and_flags_uncancelled_leg():
    order = "00000000-65ea-4b0b-048d-62f90015549e"
    report = vbr.classify_positions([_pos("P3", order)], {}, now=NOW)
    assert report[0]["critical"] and not report[0]["in_db"]
    assert report[0]["uncancelled_leg_order"] == "hypothesis4_v2"


def test_position_without_stop_is_critical():
    report = vbr.classify_positions([_pos("P4", stop=None)], {"P4": (4, "x", "EURUSD", "ouvert")}, now=NOW)
    assert report[0]["critical"] and report[0]["risk_quote_currency"] is None


def test_assert_demo_refuses_live():
    with pytest.raises(SystemExit):
        vbr.assert_demo(SimpleNamespace(capital_environment="live"))
    vbr.assert_demo(SimpleNamespace(capital_environment="demo"))
