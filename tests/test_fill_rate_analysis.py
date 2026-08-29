"""Tests de fill_rate_analysis.py (Mesure A, point 10, 29/08/2026, voir docs/DECISIONS.md)."""

import pytest

from src.db import connection_scope, init_db
from src.fill_rate_analysis import aggregate_fill_rate_by_hypothesis_asset, wilson_upper_bound


# ---------------------------------------------------------------------------
# wilson_upper_bound
# ---------------------------------------------------------------------------

def test_wilson_upper_bound_rejects_non_positive_n():
    with pytest.raises(ValueError):
        wilson_upper_bound(successes=0, n=0)


def test_wilson_upper_bound_rejects_successes_out_of_range():
    with pytest.raises(ValueError):
        wilson_upper_bound(successes=11, n=10)
    with pytest.raises(ValueError):
        wilson_upper_bound(successes=-1, n=10)


def test_wilson_upper_bound_known_value():
    # Valeur de reference calculee independamment via la formule
    # standard (Wilson 95% bilateral, p_hat=0.9, n=30).
    assert wilson_upper_bound(successes=27, n=30) == pytest.approx(0.9654, abs=1e-3)


def test_wilson_upper_bound_perfect_record_is_exactly_one():
    # Identite algebrique de la formule de Wilson : p_hat=1 -> borne
    # haute = 1.0 exactement, quel que soit n (jamais > 1, jamais deviné).
    assert wilson_upper_bound(successes=30, n=30) == pytest.approx(1.0)


def test_wilson_upper_bound_near_perfect_record_stays_under_one():
    assert wilson_upper_bound(successes=29, n=30) < 1.0
    assert wilson_upper_bound(successes=29, n=30) > 0.85


# ---------------------------------------------------------------------------
# aggregate_fill_rate_by_hypothesis_asset
# ---------------------------------------------------------------------------

def _insert_trade(conn, source, actif, statut, annulation_motif=None):
    conn.execute(
        "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
        "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
        "pourcentage_risque_applique, ouvert_at, statut, annulation_motif) "
        "VALUES (NULL, ?, ?, 'demo', 'long', 0.01, 100.0, 99.0, 99.0, 10.0, 2.0, "
        "'2026-08-29T00:00:00Z', ?, ?)",
        (source, actif, statut, annulation_motif),
    )


def test_aggregate_counts_a_b_c_correctly(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis_v2", "GOLD", "ferme")
        _insert_trade(conn, "hypothesis_v2", "GOLD", "ouvert")
        _insert_trade(conn, "hypothesis_v2", "GOLD", "annule", "peremption_marche")
        _insert_trade(conn, "hypothesis_v2", "GOLD", "annule", "rate_limit_429")
        _insert_trade(conn, "hypothesis_v2", "GOLD", "annule", "stop_refuse")

    rows = aggregate_fill_rate_by_hypothesis_asset(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "hypothesis_v2"
    assert row["actif"] == "GOLD"
    assert row["rempli"] == 2
    assert row["peremption_marche"] == 1
    assert row["echec_placement"] == 2
    assert row["n_b_plus_c"] == 3
    assert row["taux_remplissage"] == pytest.approx(2 / 3)
    assert row["verdict"] == "n_insuffisant"  # n=3 < 30


def test_aggregate_ignores_annule_rows_without_known_motif(tmp_path):
    # Trades antérieurs au point 10 - jamais rétro-catégorisés, jamais
    # comptés dans a, b, ou c (l'info n'existe nulle part).
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis", "GOLD", "annule", None)
    rows = aggregate_fill_rate_by_hypothesis_asset(db_path)
    assert rows[0]["echec_placement"] == 0
    assert rows[0]["peremption_marche"] == 0
    assert rows[0]["n_b_plus_c"] == 0
    assert rows[0]["verdict"] == "n_insuffisant"
    assert rows[0]["taux_remplissage"] is None
    assert rows[0]["wilson_borne_haute"] is None


def test_aggregate_verdict_infidele_when_wilson_upper_below_threshold(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for _ in range(15):
            _insert_trade(conn, "hypothesis_v2", "US30", "ferme")
        for _ in range(20):
            _insert_trade(conn, "hypothesis_v2", "US30", "annule", "peremption_marche")
    rows = aggregate_fill_rate_by_hypothesis_asset(db_path)
    row = rows[0]
    assert row["n_b_plus_c"] == 35
    assert row["taux_remplissage"] == pytest.approx(15 / 35)
    assert row["verdict"] == "infidele"


def test_aggregate_verdict_fidele_when_wilson_upper_above_threshold(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for _ in range(38):
            _insert_trade(conn, "hypothesis_v2", "EURUSD", "ferme")
        for _ in range(2):
            _insert_trade(conn, "hypothesis_v2", "EURUSD", "annule", "peremption_marche")
    rows = aggregate_fill_rate_by_hypothesis_asset(db_path)
    row = rows[0]
    assert row["n_b_plus_c"] == 40
    assert row["verdict"] == "fidele"


def test_aggregate_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert aggregate_fill_rate_by_hypothesis_asset(db_path) == []


def test_aggregate_groups_separately_by_source_and_asset(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis_v2", "GOLD", "ferme")
        _insert_trade(conn, "hypothesis3_v2", "GOLD", "ferme")
        _insert_trade(conn, "hypothesis_v2", "USDJPY", "ferme")
    rows = aggregate_fill_rate_by_hypothesis_asset(db_path)
    keys = {(r["source"], r["actif"]) for r in rows}
    assert keys == {("hypothesis_v2", "GOLD"), ("hypothesis3_v2", "GOLD"), ("hypothesis_v2", "USDJPY")}
