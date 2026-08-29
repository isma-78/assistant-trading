"""Tests de spread_analysis.py (point 6, 29/08/2026, voir docs/DECISIONS.md)."""

import pytest

from src.db import connection_scope, init_db
from src.spread_analysis import (
    cross_triggers_with_spread,
    hourly_spread_by_asset,
    hourly_trigger_distribution_by_source_asset,
    utc_hour_from_timestamp,
)


# ---------------------------------------------------------------------------
# utc_hour_from_timestamp
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("timestamp, expected", [
    ("2026-06-01T00:00:00Z", 0),
    ("2026-06-01T14:00:00Z", 14),
    ("2026-06-01T23:59:59Z", 23),
    ("2026-08-16T19:03:27.857413+00:00", 19),
])
def test_utc_hour_from_timestamp(timestamp, expected):
    assert utc_hour_from_timestamp(timestamp) == expected


def test_utc_hour_from_timestamp_rejects_malformed_hour():
    with pytest.raises(ValueError):
        utc_hour_from_timestamp("2026-06-01T99:00:00Z")


# ---------------------------------------------------------------------------
# hourly_spread_by_asset / hourly_trigger_distribution_by_source_asset
# ---------------------------------------------------------------------------

_next_msg_id = [0]


def _insert_signal_with_snapshot(conn, source, actif, created_at, spread=None, captured_at=None):
    _next_msg_id[0] += 1
    raw_id = conn.execute(
        "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
        "VALUES (?, 'x', ?, 't', 'signal')", (_next_msg_id[0], created_at),
    ).lastrowid
    signal_id = conn.execute(
        "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
        "confiance, statut, created_at) "
        "VALUES (?, ?, ?, 'long', 1.0, 1.0, 0.9, 1.0, 'a_valider', ?)",
        (raw_id, source, actif, created_at),
    ).lastrowid
    if spread is not None:
        conn.execute(
            "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) "
            "VALUES (?, 1.0, ?, ?, ?)",
            (signal_id, 1.0 + spread, spread, captured_at or created_at),
        )
    return signal_id


def test_hourly_spread_by_asset_groups_and_averages(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_signal_with_snapshot(conn, "hypothesis_v2", "GOLD", "2026-06-01T14:00:00Z", spread=1.0)
        _insert_signal_with_snapshot(conn, "hypothesis_v2", "GOLD", "2026-06-01T14:30:00Z", spread=3.0)
        _insert_signal_with_snapshot(conn, "hypothesis_v2", "GOLD", "2026-06-01T06:00:00Z", spread=0.5)

    rows = hourly_spread_by_asset(db_path)
    assert {"actif": "GOLD", "heure_utc": 14, "n": 2, "spread_moyen": pytest.approx(2.0)} in rows
    assert {"actif": "GOLD", "heure_utc": 6, "n": 1, "spread_moyen": pytest.approx(0.5)} in rows


def test_hourly_spread_by_asset_excludes_missing_spread(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_signal_with_snapshot(conn, "hypothesis_v2", "GOLD", "2026-06-01T14:00:00Z", spread=None)
    assert hourly_spread_by_asset(db_path) == []


def test_hourly_spread_by_asset_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert hourly_spread_by_asset(db_path) == []


def test_hourly_trigger_distribution_by_source_asset(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_signal_with_snapshot(conn, "hypothesis_v2", "GOLD", "2026-06-01T14:00:00Z")
        _insert_signal_with_snapshot(conn, "hypothesis_v2", "GOLD", "2026-06-01T14:30:00Z")
        _insert_signal_with_snapshot(conn, "hypothesis3_v2", "GOLD", "2026-06-01T06:00:00Z")

    rows = hourly_trigger_distribution_by_source_asset(db_path)
    assert {"source": "hypothesis_v2", "actif": "GOLD", "heure_utc": 14, "n": 2} in rows
    assert {"source": "hypothesis3_v2", "actif": "GOLD", "heure_utc": 6, "n": 1} in rows


def test_hourly_trigger_distribution_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert hourly_trigger_distribution_by_source_asset(db_path) == []


# ---------------------------------------------------------------------------
# cross_triggers_with_spread
# ---------------------------------------------------------------------------

def test_cross_triggers_with_spread_annotates_known_combination():
    triggers = [{"source": "hypothesis_v2", "actif": "GOLD", "heure_utc": 14, "n": 5}]
    spread_curve = [{"actif": "GOLD", "heure_utc": 14, "n": 20, "spread_moyen": 2.5}]
    result = cross_triggers_with_spread(triggers, spread_curve)
    assert result == [{"source": "hypothesis_v2", "actif": "GOLD", "heure_utc": 14, "n": 5, "spread_moyen_a_cette_heure": 2.5}]


def test_cross_triggers_with_spread_none_when_combination_never_observed():
    triggers = [{"source": "hypothesis_v2", "actif": "GOLD", "heure_utc": 3, "n": 1}]
    result = cross_triggers_with_spread(triggers, spread_curve=[])
    assert result[0]["spread_moyen_a_cette_heure"] is None
