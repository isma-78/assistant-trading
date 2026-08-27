"""
Tests de causal_decomposition.py (27/08/2026, voir docs/DECISIONS.md).
Couche pure (decompose_trade_leg, aggregate_trade_decomposition) : 100%
de couverture. Orchestration (compute_.../persist_.../aggregate_by_...) :
testée avec une DB temporaire réelle.
"""

import pytest

from src.causal_decomposition import (
    TradeLegDecomposition,
    aggregate_by_hypothesis_asset_month,
    aggregate_trade_decomposition,
    compute_trade_causal_decomposition,
    decompose_trade_leg,
    persist_trade_causal_decomposition,
)
from src.db import connection_scope, init_db


# ---------------------------------------------------------------------------
# decompose_trade_leg
# ---------------------------------------------------------------------------

def test_decompose_leg_rejects_non_positive_stop_distance():
    with pytest.raises(ValueError):
        decompose_trade_leg("long", 100.0, 100.0, 101.0, 101.0, 0.0, 1.0)


def test_decompose_leg_rejects_unknown_direction():
    with pytest.raises(ValueError):
        decompose_trade_leg("sideways", 100.0, 100.0, 101.0, 101.0, 1.0, 1.0)


def test_decompose_leg_long_theoretical_r():
    # Long, entrée théorique 100, sortie théorique 102, stop=1 -> R théorique = 2.
    leg = decompose_trade_leg("long", 100.0, None, 102.0, None, 1.0, r_realized=2.0)
    assert leg.r_theoretical == pytest.approx(2.0)
    assert leg.cout_entree is None
    assert leg.cout_sortie is None
    assert leg.derive_gestion is None


def test_decompose_leg_short_theoretical_r():
    # Short, entrée théorique 100, sortie théorique 98, stop=1 -> R théorique = 2.
    leg = decompose_trade_leg("short", 100.0, None, 98.0, None, 1.0, r_realized=2.0)
    assert leg.r_theoretical == pytest.approx(2.0)


def test_decompose_leg_long_unfavorable_entry_fill_is_positive_cost():
    # Long, rempli PLUS CHER que demandé (100.1 au lieu de 100) -> coût positif.
    leg = decompose_trade_leg("long", 100.0, 100.1, 102.0, None, stop_distance=1.0, r_realized=1.9)
    assert leg.cout_entree == pytest.approx(0.1)


def test_decompose_leg_short_unfavorable_entry_fill_is_positive_cost():
    # Short, rempli à un prix DE VENTE plus bas que demandé (99.9 au lieu de 100) -> défavorable.
    leg = decompose_trade_leg("short", 100.0, 99.9, 98.0, None, stop_distance=1.0, r_realized=1.9)
    assert leg.cout_entree == pytest.approx(0.1)


def test_decompose_leg_long_unfavorable_exit_fill_is_positive_cost():
    # Long, sortie réelle plus basse que la théorique (101.9 au lieu de 102) -> défavorable.
    leg = decompose_trade_leg("long", 100.0, 100.0, 102.0, 101.9, stop_distance=1.0, r_realized=1.9)
    assert leg.cout_sortie == pytest.approx(0.1)


def test_decompose_leg_short_unfavorable_exit_fill_is_positive_cost():
    # Short, rachat réel plus cher que la théorique (98.1 au lieu de 98) -> défavorable.
    leg = decompose_trade_leg("short", 100.0, 100.0, 98.0, 98.1, stop_distance=1.0, r_realized=1.9)
    assert leg.cout_sortie == pytest.approx(0.1)


def test_decompose_leg_derive_gestion_is_residual_identity():
    # R_réalisé = R_théorique - coût_entrée - coût_sortie - dérive_gestion
    # -> dérive_gestion se déduit arithmétiquement, jamais mesuré à part.
    leg = decompose_trade_leg("long", 100.0, 100.1, 102.0, 101.8, stop_distance=1.0, r_realized=1.5)
    # r_theoretical = 2.0 ; cout_entree = 0.1 ; cout_sortie = 0.2
    assert leg.r_theoretical == pytest.approx(2.0)
    assert leg.cout_entree == pytest.approx(0.1)
    assert leg.cout_sortie == pytest.approx(0.2)
    # 1.5 == 2.0 - 0.1 - 0.2 - derive => derive = 0.2
    assert leg.derive_gestion == pytest.approx(0.2)


def test_decompose_leg_derive_none_without_exit_real():
    leg = decompose_trade_leg("long", 100.0, 100.1, 102.0, None, stop_distance=1.0, r_realized=1.5)
    assert leg.cout_sortie is None
    assert leg.derive_gestion is None


def test_decompose_leg_derive_none_without_entry_real():
    leg = decompose_trade_leg("long", 100.0, None, 102.0, 101.8, stop_distance=1.0, r_realized=1.5)
    assert leg.cout_entree is None
    assert leg.derive_gestion is None


# ---------------------------------------------------------------------------
# aggregate_trade_decomposition
# ---------------------------------------------------------------------------

def test_aggregate_rejects_empty_legs():
    with pytest.raises(ValueError):
        aggregate_trade_decomposition([])


def test_aggregate_weights_by_fraction():
    leg1 = TradeLegDecomposition(r_theoretical=1.0, cout_entree=0.1, cout_sortie=0.05, derive_gestion=0.0)
    leg2 = TradeLegDecomposition(r_theoretical=2.0, cout_entree=0.1, cout_sortie=0.05, derive_gestion=0.0)
    result = aggregate_trade_decomposition([(0.5, leg1), (0.5, leg2)])
    assert result.r_theoretical == pytest.approx(1.5)
    assert result.cout_entree == pytest.approx(0.1)
    assert result.cout_sortie == pytest.approx(0.05)
    assert result.derive_gestion == pytest.approx(0.0)


def test_aggregate_component_none_if_any_leg_missing_it():
    leg1 = TradeLegDecomposition(r_theoretical=1.0, cout_entree=0.1, cout_sortie=0.05, derive_gestion=0.0)
    leg2 = TradeLegDecomposition(r_theoretical=2.0, cout_entree=0.1, cout_sortie=None, derive_gestion=None)
    result = aggregate_trade_decomposition([(0.5, leg1), (0.5, leg2)])
    assert result.cout_entree == pytest.approx(0.1)  # présent sur les deux jambes
    assert result.cout_sortie is None  # manquant sur une jambe -> tout le trade est None
    assert result.derive_gestion is None


# ---------------------------------------------------------------------------
# Orchestration (DB réelle)
# ---------------------------------------------------------------------------

def _insert_trade_with_partials(db_path, direction="long", entry_prevu=100.0, entry_reel=100.0,
                                 stop_initial=99.0, partials=None, actif="GOLD", source="hypothesis5"):
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, ferme_at, statut) "
            "VALUES (NULL, ?, ?, 'demo', ?, 0.01, ?, ?, ?, ?, 10.0, 2.0, "
            "'2026-06-01T00:00:00Z', '2026-06-02T00:00:00Z', 'ferme')",
            (source, actif, direction, entry_prevu, entry_reel, stop_initial, stop_initial),
        ).lastrowid
        for fraction, r_atteint, prix_sortie, prix_sortie_reel in partials:
            conn.execute(
                "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, executed_at, prix_sortie_reel) "
                "VALUES (?, 'tp', ?, ?, ?, '2026-06-02T00:00:00Z', ?)",
                (trade_id, fraction, prix_sortie, r_atteint, prix_sortie_reel),
            )
    return trade_id


def test_compute_returns_none_for_missing_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert compute_trade_causal_decomposition(db_path, 999) is None


def test_compute_returns_none_without_partials(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(db_path, partials=[])
    assert compute_trade_causal_decomposition(db_path, trade_id) is None


def test_compute_returns_none_when_stop_distance_zero(tmp_path):
    # Donnée corrompue (stop == entrée) : fail-safe, jamais une division par zéro.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, entry_prevu=100.0, entry_reel=100.0, stop_initial=100.0,
        partials=[(1.0, 0.0, 101.0, 101.0)],
    )
    assert compute_trade_causal_decomposition(db_path, trade_id) is None


def test_compute_single_leg_long_trade(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # Long, entrée prévue=100 réelle=100.1, stop=99 (distance=1), sortie théorique=102 réelle=101.8.
    trade_id = _insert_trade_with_partials(
        db_path, direction="long", entry_prevu=100.0, entry_reel=100.1, stop_initial=99.0,
        partials=[(1.0, 1.5, 102.0, 101.8)],
    )
    result = compute_trade_causal_decomposition(db_path, trade_id)
    assert result is not None
    assert result.r_theoretical == pytest.approx(2.0)
    assert result.cout_entree == pytest.approx(0.1)
    assert result.cout_sortie == pytest.approx(0.2)
    assert result.derive_gestion == pytest.approx(0.2)


def test_compute_multi_leg_trade_missing_one_exit_real_makes_sortie_none(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, direction="long", entry_prevu=100.0, entry_reel=100.0, stop_initial=99.0,
        partials=[(0.5, 1.0, 101.0, 100.9), (0.5, 2.0, 102.0, None)],
    )
    result = compute_trade_causal_decomposition(db_path, trade_id)
    assert result is not None
    assert result.cout_entree == pytest.approx(0.0)
    assert result.cout_sortie is None
    assert result.derive_gestion is None


def test_persist_and_aggregate_by_hypothesis_asset_month(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, direction="long", entry_prevu=100.0, entry_reel=100.1, stop_initial=99.0,
        partials=[(1.0, 1.5, 102.0, 101.8)], actif="GOLD", source="hypothesis5",
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-02T00:00:00Z")

    rows = aggregate_by_hypothesis_asset_month(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "hypothesis5"
    assert row["actif"] == "GOLD"
    assert row["mois"] == "2026-06"
    assert row["n"] == 1
    assert row["cout_entree_moyen"] == pytest.approx(0.1)
    assert row["cout_sortie_moyen"] == pytest.approx(0.2)


def test_persist_is_idempotent_replace(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, partials=[(1.0, 1.0, 101.0, 101.0)],
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-02T00:00:00Z")
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-02T00:00:01Z")

    with connection_scope(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM trade_causal_decomposition WHERE trade_id = ?", (trade_id,)).fetchone()["n"]
    assert n == 1


def test_aggregate_with_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert aggregate_by_hypothesis_asset_month(db_path) == []
