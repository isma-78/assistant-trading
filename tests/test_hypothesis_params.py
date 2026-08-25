import types

import pytest

from src.db import connection_scope, get_connection, init_db
from src.hypothesis_params import (
    apply_bollinger_std_override,
    apply_overrides,
    get_active_override,
    get_resolution_override,
)


def _insert_rule_change(db_path, variable, value, statut="applique", applied_at="2026-08-25T00:00:00"):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO rule_changes (proposed_at, variable, constat_stat, ajustement_propose, statut, applied_at) "
            "VALUES (?, ?, '{}', ?, ?, ?)",
            ("2026-08-25T00:00:00", variable, value, statut, applied_at),
        )


def test_get_active_override_absent(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def test_get_active_override_present(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H5.RSI_PERIOD", "9")
    assert get_active_override(db_path, "H5", "RSI_PERIOD") == "9"


def test_get_active_override_ignores_non_applique(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H5.RSI_PERIOD", "9", statut="propose")
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def test_get_active_override_takes_most_recent(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H5.RSI_PERIOD", "9", applied_at="2026-08-25T00:00:00")
    _insert_rule_change(db_path, "H5.RSI_PERIOD", "21", applied_at="2026-08-26T00:00:00")
    assert get_active_override(db_path, "H5", "RSI_PERIOD") == "21"


def test_get_active_override_isolates_hypothesis(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H4.RSI_PERIOD", "9")
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def test_get_active_override_missing_db_is_safe(tmp_path):
    db_path = str(tmp_path / "does_not_exist" / "t.db")
    assert get_active_override(db_path, "H5", "RSI_PERIOD") is None


def _fake_module(**attrs):
    return types.SimpleNamespace(**attrs)


def test_apply_overrides_no_data(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    module = _fake_module(RSI_PERIOD=14, TP1_R_MULTIPLE=1.0)
    applied = apply_overrides(module, "H5", db_path, ["RSI_PERIOD", "TP1_R_MULTIPLE"])
    assert applied == {}
    assert module.RSI_PERIOD == 14
    assert module.TP1_R_MULTIPLE == 1.0


def test_apply_overrides_applies_typed_value(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H5.RSI_PERIOD", "9")
    _insert_rule_change(db_path, "H5.TP1_R_MULTIPLE", "0.5")
    module = _fake_module(RSI_PERIOD=14, TP1_R_MULTIPLE=1.0)
    applied = apply_overrides(module, "H5", db_path, ["RSI_PERIOD", "TP1_R_MULTIPLE"])
    assert applied == {"RSI_PERIOD": 9, "TP1_R_MULTIPLE": 0.5}
    assert module.RSI_PERIOD == 9
    assert isinstance(module.RSI_PERIOD, int)
    assert module.TP1_R_MULTIPLE == pytest.approx(0.5)


def test_apply_overrides_skips_unknown_attribute(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H5.DOES_NOT_EXIST", "9")
    module = _fake_module(RSI_PERIOD=14)
    applied = apply_overrides(module, "H5", db_path, ["DOES_NOT_EXIST"])
    assert applied == {}


def test_apply_overrides_skips_non_convertible_value(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H5.RSI_PERIOD", "not_a_number")
    module = _fake_module(RSI_PERIOD=14)
    applied = apply_overrides(module, "H5", db_path, ["RSI_PERIOD"])
    assert applied == {}
    assert module.RSI_PERIOD == 14


def test_apply_bollinger_std_override_absent(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)

    def compute_bollinger_bands(candles, period=20, std_multiplier=2.0):
        return period, std_multiplier

    module = _fake_module(compute_bollinger_bands=compute_bollinger_bands, BOLLINGER_STD_MULTIPLIER=2.0)
    result = apply_bollinger_std_override("H4", db_path, module)
    assert result is None
    assert module.compute_bollinger_bands.__defaults__ == (20, 2.0)


def test_apply_bollinger_std_override_applies(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H4.BOLLINGER_STD_MULTIPLIER", "2.5")

    def compute_bollinger_bands(candles, period=20, std_multiplier=2.0):
        return period, std_multiplier

    module = _fake_module(compute_bollinger_bands=compute_bollinger_bands, BOLLINGER_STD_MULTIPLIER=2.0)
    result = apply_bollinger_std_override("H4", db_path, module)
    assert result == pytest.approx(2.5)
    assert module.compute_bollinger_bands.__defaults__ == (20, 2.5)
    assert module.BOLLINGER_STD_MULTIPLIER == pytest.approx(2.5)


def test_apply_bollinger_std_override_non_convertible(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H4.BOLLINGER_STD_MULTIPLIER", "not_a_float")

    def compute_bollinger_bands(candles, period=20, std_multiplier=2.0):
        return period, std_multiplier

    module = _fake_module(compute_bollinger_bands=compute_bollinger_bands, BOLLINGER_STD_MULTIPLIER=2.0)
    result = apply_bollinger_std_override("H4", db_path, module)
    assert result is None


def test_apply_bollinger_std_override_no_defaults(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H4.BOLLINGER_STD_MULTIPLIER", "2.5")

    def compute_bollinger_bands(candles):
        return candles

    module = _fake_module(compute_bollinger_bands=compute_bollinger_bands, BOLLINGER_STD_MULTIPLIER=2.0)
    result = apply_bollinger_std_override("H4", db_path, module)
    assert result is None


def test_get_resolution_override_default(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_resolution_override(db_path, "H3", "entree", "MINUTE_15") == "MINUTE_15"


def test_get_resolution_override_applied(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H3.resolution_entree", "HOUR")
    assert get_resolution_override(db_path, "H3", "entree", "MINUTE_15") == "HOUR"


def test_get_resolution_override_axis_isolated(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_rule_change(db_path, "H3.resolution_confirmation", "HOUR")
    assert get_resolution_override(db_path, "H3", "entree", "MINUTE_15") == "MINUTE_15"
    assert get_resolution_override(db_path, "H3", "confirmation", "MINUTE_15") == "HOUR"
