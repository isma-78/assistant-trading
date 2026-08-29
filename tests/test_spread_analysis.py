"""Tests de spread_analysis.py (point 6, 29/08/2026, voir docs/DECISIONS.md)."""

import pytest

from src.db import connection_scope, init_db
from src.spread_analysis import (
    compute_expensive_hour_cost,
    compute_expensive_hours,
    compute_expensive_hours_by_asset,
    cross_triggers_with_spread,
    hourly_spread_by_asset,
    hourly_trigger_distribution_by_source_asset,
    is_expensive_hour,
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


# ---------------------------------------------------------------------------
# compute_expensive_hours / compute_expensive_hours_by_asset / is_expensive_hour
# ---------------------------------------------------------------------------

def _flat_curve(actif, spread, hours=range(24)):
    return [{"actif": actif, "heure_utc": h, "n": 10, "spread_moyen": spread} for h in hours]


def test_compute_expensive_hours_empty_input_returns_empty_set():
    assert compute_expensive_hours([]) == set()


def test_compute_expensive_hours_flat_curve_has_no_expensive_hour():
    # Toutes les heures egales a la mediane -> aucune >= 2x la mediane.
    rows = _flat_curve("GOLD", 0.36)
    assert compute_expensive_hours(rows) == set()


def test_compute_expensive_hours_detects_real_spike_pattern():
    # Reproduit approximativement la courbe GBPUSD mesuree le 29/08/2026 :
    # plat ~0.00014, spike x7 a 21h, x2 a 22h.
    rows = _flat_curve("GBPUSD", 0.00014, hours=range(21))
    rows += [
        {"actif": "GBPUSD", "heure_utc": 21, "n": 35, "spread_moyen": 0.00096},
        {"actif": "GBPUSD", "heure_utc": 22, "n": 25, "spread_moyen": 0.00026},
        {"actif": "GBPUSD", "heure_utc": 23, "n": 40, "spread_moyen": 0.00014},
    ]
    expensive = compute_expensive_hours(rows)
    assert 21 in expensive
    assert 22 not in expensive  # x1.86, sous le seuil x2
    assert 0 not in expensive


def test_compute_expensive_hours_zero_median_returns_empty_set():
    rows = _flat_curve("X", 0.0)
    assert compute_expensive_hours(rows) == set()


def test_compute_expensive_hours_custom_threshold():
    rows = _flat_curve("GBPUSD", 0.0001, hours=range(23))
    rows.append({"actif": "GBPUSD", "heure_utc": 23, "n": 10, "spread_moyen": 0.00015})  # x1.5
    assert compute_expensive_hours(rows, threshold=2.0) == set()
    assert compute_expensive_hours(rows, threshold=1.4) == {23}


def test_compute_expensive_hours_by_asset_end_to_end(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for h in range(24):
            spread = 0.00096 if h == 21 else 0.00014
            _insert_signal_with_snapshot(conn, "hypothesis_v2", "GBPUSD", f"2026-06-01T{h:02d}:00:00Z", spread=spread)
    result = compute_expensive_hours_by_asset(db_path)
    assert result == {"GBPUSD": {21}}


def test_compute_expensive_hours_by_asset_no_data_returns_empty_dict(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert compute_expensive_hours_by_asset(db_path) == {}


def test_is_expensive_hour():
    mapping = {"GBPUSD": {21, 22}}
    assert is_expensive_hour("GBPUSD", 21, mapping) is True
    assert is_expensive_hour("GBPUSD", 10, mapping) is False
    assert is_expensive_hour("USDJPY", 21, mapping) is False  # actif absent de la carte


# ---------------------------------------------------------------------------
# compute_expensive_hour_cost
# ---------------------------------------------------------------------------

def _insert_trade_for_cost(conn, source, actif, ouvert_at, entree, stop):
    conn.execute(
        "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
        "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
        "pourcentage_risque_applique, ouvert_at, statut) "
        "VALUES (NULL, ?, ?, 'demo', 'long', 0.01, ?, ?, ?, 10.0, 2.0, ?, 'ferme')",
        (source, actif, entree, stop, stop, ouvert_at),
    )


def test_compute_expensive_hour_cost_computes_differential_over_real_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        # Courbe : plat 0.0001 sauf 21h a 0.0005 (x5).
        for h in range(24):
            spread = 0.0005 if h == 21 else 0.0001
            _insert_signal_with_snapshot(conn, "hypothesis_v2", "GBPUSD", f"2026-06-01T{h:02d}:00:00Z", spread=spread)
        # Un trade reel ouvert a 21h, stop_distance=0.01 (entree=1.30, stop=1.29).
        _insert_trade_for_cost(conn, "hypothesis_v2", "GBPUSD", "2026-06-02T21:00:00Z", 1.30, 1.29)

    expensive_hours = compute_expensive_hours_by_asset(db_path)
    assert expensive_hours == {"GBPUSD": {21}}

    result = compute_expensive_hour_cost(db_path, expensive_hours)
    assert len(result) == 1
    row = result[0]
    assert row["source"] == "hypothesis_v2"
    assert row["actif"] == "GBPUSD"
    assert row["n_trades_heure_chere"] == 1
    assert row["cout_r_calculable"] == 1
    # (0.0005 - 0.0001) / 0.01 = 0.04R
    assert row["cout_r_total"] == pytest.approx(0.04)


def test_compute_expensive_hour_cost_ignores_trades_outside_expensive_hours(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for h in range(24):
            spread = 0.0005 if h == 21 else 0.0001
            _insert_signal_with_snapshot(conn, "hypothesis_v2", "GBPUSD", f"2026-06-01T{h:02d}:00:00Z", spread=spread)
        _insert_trade_for_cost(conn, "hypothesis_v2", "GBPUSD", "2026-06-02T10:00:00Z", 1.30, 1.29)

    expensive_hours = compute_expensive_hours_by_asset(db_path)
    result = compute_expensive_hour_cost(db_path, expensive_hours)
    assert result == []


def test_compute_expensive_hour_cost_no_expensive_hours_for_asset_returns_nothing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade_for_cost(conn, "hypothesis_v2", "GOLD", "2026-06-02T21:00:00Z", 2400.0, 2380.0)
    result = compute_expensive_hour_cost(db_path, expensive_hours_by_asset={})
    assert result == []


def test_compute_expensive_hour_cost_zero_stop_distance_not_counted_as_calculable(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for h in range(24):
            spread = 0.0005 if h == 21 else 0.0001
            _insert_signal_with_snapshot(conn, "hypothesis_v2", "GBPUSD", f"2026-06-01T{h:02d}:00:00Z", spread=spread)
        _insert_trade_for_cost(conn, "hypothesis_v2", "GBPUSD", "2026-06-02T21:00:00Z", 1.30, 1.30)  # stop == entree

    expensive_hours = compute_expensive_hours_by_asset(db_path)
    result = compute_expensive_hour_cost(db_path, expensive_hours)
    assert result[0]["n_trades_heure_chere"] == 1
    assert result[0]["cout_r_calculable"] == 0
    assert result[0]["cout_r_total"] == 0.0
