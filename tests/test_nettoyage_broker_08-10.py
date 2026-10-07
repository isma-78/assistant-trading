"""Tests de scripts/nettoyage_broker_08-10.py — règle des 120 minutes
(reprise du 08/10/2026) et classement des positions."""

import importlib.util
import os
from datetime import datetime, timedelta, timezone

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "nettoyage_broker_08-10.py")
_spec = importlib.util.spec_from_file_location("nettoyage_broker_08_10", _PATH)
nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nb)


NOW = datetime(2026, 10, 8, 0, 0, 0, tzinfo=timezone.utc)


def _pos(created_iso=None):
    return {"createdDateUTC": created_iso} if created_iso is not None else {}


# --- position_age_minutes ---------------------------------------------------

def test_position_age_minutes_missing_field_returns_none():
    assert nb.position_age_minutes(_pos(), NOW) is None


def test_position_age_minutes_computes_elapsed_minutes():
    created = (NOW - timedelta(minutes=150)).isoformat()
    age = nb.position_age_minutes(_pos(created), NOW)
    assert age == pytest.approx(150.0, abs=0.01)


def test_position_age_minutes_handles_z_suffix_and_naive_datetime():
    created = (NOW - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    age = nb.position_age_minutes(_pos(created), NOW)
    assert age == pytest.approx(30.0, abs=0.01)


def test_position_age_minutes_naive_now_is_treated_as_utc():
    created = (NOW - timedelta(minutes=10)).isoformat()
    naive_now = NOW.replace(tzinfo=None)
    age = nb.position_age_minutes(_pos(created), naive_now)
    assert age == pytest.approx(10.0, abs=0.01)


# --- is_confirmed_orphan -----------------------------------------------------

def test_is_confirmed_orphan_true_when_old_enough():
    created = (NOW - timedelta(minutes=121)).isoformat()
    assert nb.is_confirmed_orphan(_pos(created), NOW) is True


def test_is_confirmed_orphan_false_when_too_recent():
    created = (NOW - timedelta(minutes=26)).isoformat()
    assert nb.is_confirmed_orphan(_pos(created), NOW) is False


def test_is_confirmed_orphan_false_when_age_unknown():
    assert nb.is_confirmed_orphan(_pos(), NOW) is False


def test_is_confirmed_orphan_exact_boundary_is_confirmed():
    created = (NOW - timedelta(minutes=120)).isoformat()
    assert nb.is_confirmed_orphan(_pos(created), NOW) is True


# --- classify_position (déjà existant, vérifié ici pour mémoire) ------------

def test_classify_position_retired_combo():
    item = {"position": {"dealId": "D1", "workingOrderId": "W1"}}
    db_index = {"D1": (14877, "hypothesis2_v2", "CHFJPY", "ouvert")}
    assert nb.classify_position(item, db_index) == "c"


def test_classify_position_uncancelled_leg_order():
    working_id = next(iter(nb.UNCANCELLED_LEG_ORDERS))
    item = {"position": {"dealId": "D2", "workingOrderId": working_id}}
    assert nb.classify_position(item, {}) == "b"


def test_classify_position_unknown_is_orphan_category_d():
    item = {"position": {"dealId": "D3", "workingOrderId": "W3"}}
    assert nb.classify_position(item, {}) == "d"


def test_classify_position_known_normal_trade_is_e():
    item = {"position": {"dealId": "D4", "workingOrderId": "W4"}}
    db_index = {"D4": (99999, "hypothesis2_v2", "GOLD", "ouvert")}
    assert nb.classify_position(item, db_index) == "e"
