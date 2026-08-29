"""Tests de episode_counter.py (Mesure B, point 11, 29/08/2026, voir docs/DECISIONS.md)."""

import pytest

from src.db import connection_scope, init_db
from src.episode_counter import aggregate_episode_counts, count_episodes


# ---------------------------------------------------------------------------
# count_episodes
# ---------------------------------------------------------------------------

def test_count_episodes_empty_list_is_zero():
    assert count_episodes([], max_gap_seconds=120) == 0


def test_count_episodes_single_timestamp_is_one_episode():
    assert count_episodes(["2026-08-29T10:00:00Z"], max_gap_seconds=120) == 1


def test_count_episodes_collapses_consecutive_attempts_within_gap():
    # 3 tentatives a 60s d'intervalle, gap max 120s -> 1 seul episode.
    ouvert_ats = ["2026-08-29T10:00:00Z", "2026-08-29T10:01:00Z", "2026-08-29T10:02:00Z"]
    assert count_episodes(ouvert_ats, max_gap_seconds=120) == 1


def test_count_episodes_splits_when_gap_exceeds_threshold():
    ouvert_ats = ["2026-08-29T10:00:00Z", "2026-08-29T10:01:00Z", "2026-08-29T14:00:00Z"]
    assert count_episodes(ouvert_ats, max_gap_seconds=120) == 2


def test_count_episodes_handles_unsorted_input():
    ouvert_ats = ["2026-08-29T10:02:00Z", "2026-08-29T10:00:00Z", "2026-08-29T10:01:00Z"]
    assert count_episodes(ouvert_ats, max_gap_seconds=120) == 1


def test_count_episodes_exactly_at_threshold_is_same_episode():
    ouvert_ats = ["2026-08-29T10:00:00Z", "2026-08-29T10:02:00Z"]  # exactement 120s
    assert count_episodes(ouvert_ats, max_gap_seconds=120) == 1


def test_count_episodes_accepts_mixed_iso_formats():
    ouvert_ats = ["2026-08-16T19:03:27.857413+00:00", "2026-08-16T19:04:00Z"]
    assert count_episodes(ouvert_ats, max_gap_seconds=120) == 1


# ---------------------------------------------------------------------------
# aggregate_episode_counts
# ---------------------------------------------------------------------------

def _insert_trade(conn, source, actif, direction, ouvert_at):
    conn.execute(
        "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
        "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
        "pourcentage_risque_applique, ouvert_at, statut) "
        "VALUES (NULL, ?, ?, 'demo', ?, 0.01, 100.0, 99.0, 99.0, 10.0, 2.0, ?, 'annule')",
        (source, actif, direction, ouvert_at),
    )


def test_aggregate_episode_counts_end_to_end(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        # 3 tentatives rapprochees (echecs de placement en rafale) -> 1 episode.
        _insert_trade(conn, "hypothesis5_v2", "GBPUSD", "long", "2026-08-27T20:06:00Z")
        _insert_trade(conn, "hypothesis5_v2", "GBPUSD", "long", "2026-08-27T20:07:00Z")
        _insert_trade(conn, "hypothesis5_v2", "GBPUSD", "long", "2026-08-27T20:08:00Z")
        # Sens oppose sur le meme actif -> groupe distinct.
        _insert_trade(conn, "hypothesis5_v2", "GBPUSD", "short", "2026-08-27T21:00:00Z")

    rows = aggregate_episode_counts(db_path, max_gap_seconds=120)
    long_row = next(r for r in rows if r["direction"] == "long")
    short_row = next(r for r in rows if r["direction"] == "short")
    assert long_row["n_lignes_brutes"] == 3
    assert long_row["n_episodes"] == 1
    assert short_row["n_lignes_brutes"] == 1
    assert short_row["n_episodes"] == 1


def test_aggregate_episode_counts_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert aggregate_episode_counts(db_path, max_gap_seconds=120) == []


def test_aggregate_episode_counts_separates_different_assets_and_sources(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis_v2", "GOLD", "long", "2026-08-29T10:00:00Z")
        _insert_trade(conn, "hypothesis3_v2", "GOLD", "long", "2026-08-29T10:00:00Z")
        _insert_trade(conn, "hypothesis_v2", "USDJPY", "long", "2026-08-29T10:00:00Z")
    rows = aggregate_episode_counts(db_path, max_gap_seconds=120)
    keys = {(r["source"], r["actif"], r["direction"]) for r in rows}
    assert keys == {
        ("hypothesis_v2", "GOLD", "long"),
        ("hypothesis3_v2", "GOLD", "long"),
        ("hypothesis_v2", "USDJPY", "long"),
    }
