"""
Tests de causal_decomposition.py (27-28/08/2026, voir docs/DECISIONS.md).
Couche pure (decompose_trade_leg, aggregate_trade_decomposition,
is_cout_sortie_plausible) : 100% de couverture. Orchestration
(compute_.../persist_.../aggregate_by_...) : testée avec une DB
temporaire réelle.
"""

import pytest

from src.causal_decomposition import (
    TradeLegDecomposition,
    aggregate_by_dimension,
    aggregate_by_hypothesis_asset_month,
    aggregate_trade_decomposition,
    classify_cost_vs_strategy,
    compute_trade_causal_decomposition,
    decompose_gestion_delay,
    decompose_trade_leg,
    duration_bucket,
    holding_duration_hours,
    is_cout_sortie_plausible,
    outcome_label,
    persist_trade_causal_decomposition,
    session_bucket_from_ouvert_at,
)
from src.db import connection_scope, init_db


# ---------------------------------------------------------------------------
# decompose_trade_leg
# ---------------------------------------------------------------------------

def test_decompose_leg_rejects_non_positive_stop_distance():
    with pytest.raises(ValueError):
        decompose_trade_leg("long", 100.0, 100.0, 101.0, 101.0, 0.0)


def test_decompose_leg_rejects_unknown_direction():
    with pytest.raises(ValueError):
        decompose_trade_leg("sideways", 100.0, 100.0, 101.0, 101.0, 1.0)


def test_decompose_leg_long_theoretical_r():
    # Long, entrée théorique 100, sortie théorique 102, stop=1 -> R théorique = 2.
    leg = decompose_trade_leg("long", 100.0, None, 102.0, None, 1.0)
    assert leg.r_theoretical == pytest.approx(2.0)
    assert leg.cout_entree is None
    assert leg.cout_sortie is None
    assert leg.derive_gestion is None


def test_decompose_leg_short_theoretical_r():
    # Short, entrée théorique 100, sortie théorique 98, stop=1 -> R théorique = 2.
    leg = decompose_trade_leg("short", 100.0, None, 98.0, None, 1.0)
    assert leg.r_theoretical == pytest.approx(2.0)


def test_decompose_leg_long_unfavorable_entry_fill_is_positive_cost():
    # Long, rempli PLUS CHER que demandé (100.1 au lieu de 100) -> coût positif.
    leg = decompose_trade_leg("long", 100.0, 100.1, 102.0, None, stop_distance=1.0)
    assert leg.cout_entree == pytest.approx(0.1)


def test_decompose_leg_short_unfavorable_entry_fill_is_positive_cost():
    # Short, rempli à un prix DE VENTE plus bas que demandé (99.9 au lieu de 100) -> défavorable.
    leg = decompose_trade_leg("short", 100.0, 99.9, 98.0, None, stop_distance=1.0)
    assert leg.cout_entree == pytest.approx(0.1)


def test_decompose_leg_long_unfavorable_exit_fill_is_positive_cost():
    # Long, sortie réelle plus basse que la théorique (101.9 au lieu de 102) -> défavorable.
    leg = decompose_trade_leg("long", 100.0, 100.0, 102.0, 101.9, stop_distance=1.0)
    assert leg.cout_sortie == pytest.approx(0.1)


def test_decompose_leg_short_unfavorable_exit_fill_is_positive_cost():
    # Short, rachat réel plus cher que la théorique (98.1 au lieu de 98) -> défavorable.
    leg = decompose_trade_leg("short", 100.0, 100.0, 98.0, 98.1, stop_distance=1.0)
    assert leg.cout_sortie == pytest.approx(0.1)


def test_decompose_leg_derive_gestion_is_always_zero_with_consistent_prices():
    # Correctif du 28/08/2026 (voir docs/DECISIONS.md, découvert en
    # vérifiant l'identité sur un trade réel, trade 14240) : le R
    # réellement réalisé est désormais calculé ICI depuis les prix
    # réels (jamais réutilisé d'un champ externe à base incohérente,
    # ancien bug) -> coût_entrée + coût_sortie expliquent alors
    # EXACTEMENT tout l'écart, par construction arithmétique.
    # dérive_gestion == 0.0 est donc l'identité ATTENDUE ici, pas une
    # coïncidence : ça confirme que le module ne mesure QUE des écarts
    # de prix, jamais un décalage de décision de gestion.
    leg = decompose_trade_leg("long", 100.0, 100.1, 102.0, 101.8, stop_distance=1.0)
    assert leg.r_theoretical == pytest.approx(2.0)
    assert leg.cout_entree == pytest.approx(0.1)
    assert leg.cout_sortie == pytest.approx(0.2)
    assert leg.derive_gestion == pytest.approx(0.0, abs=1e-9)


def test_decompose_leg_derive_none_without_exit_real():
    leg = decompose_trade_leg("long", 100.0, 100.1, 102.0, None, stop_distance=1.0)
    assert leg.cout_sortie is None
    assert leg.derive_gestion is None


def test_decompose_leg_derive_none_without_entry_real():
    leg = decompose_trade_leg("long", 100.0, None, 102.0, 101.8, stop_distance=1.0)
    assert leg.cout_entree is None
    assert leg.derive_gestion is None


# ---------------------------------------------------------------------------
# decompose_gestion_delay (28/08/2026, point 2)
# ---------------------------------------------------------------------------

def test_gestion_delay_none_without_p_declenchement():
    assert decompose_gestion_delay("long", 102.0, None, 101.8, 1.0) == (None, None)


def test_gestion_delay_none_without_exit_real():
    assert decompose_gestion_delay("long", 102.0, 101.9, None, 1.0) == (None, None)


def test_gestion_delay_rejects_unknown_direction():
    with pytest.raises(ValueError):
        decompose_gestion_delay("sideways", 102.0, 101.9, 101.8, 1.0)


def test_gestion_delay_long_splits_cout_sortie_exactly():
    # Long : theorique=102, vu a la decision=101.9 (deja depasse de 0.1),
    # reellement execute=101.8 (encore 0.1 de moins). stop=1.
    survol, delai = decompose_gestion_delay("long", 102.0, 101.9, 101.8, 1.0)
    assert survol == pytest.approx(0.1)   # 102 - 101.9
    assert delai == pytest.approx(0.1)    # 101.9 - 101.8
    # Identite : survol + delai == cout_sortie (102-101.8)/1 = 0.2
    assert survol + delai == pytest.approx((102.0 - 101.8) / 1.0)


def test_gestion_delay_short_splits_cout_sortie_exactly():
    # Short : theorique=98, vu a la decision=98.1 (deja depasse, favorable
    # pour un short), reellement execute=98.2 (encore plus favorable). stop=1.
    survol, delai = decompose_gestion_delay("short", 98.0, 98.1, 98.2, 1.0)
    assert survol == pytest.approx(0.1)   # -(98-98.1)
    assert delai == pytest.approx(0.1)    # -(98.1-98.2)
    assert survol + delai == pytest.approx(-(98.0 - 98.2) / 1.0)


def test_decompose_leg_propagates_gestion_delay_fields():
    leg = decompose_trade_leg("long", 100.0, 100.0, 102.0, 101.8, 1.0, p_declenchement=101.9)
    assert leg.survol_polling == pytest.approx(0.1)
    assert leg.delai_broker == pytest.approx(0.1)


def test_decompose_leg_gestion_delay_none_without_p_declenchement():
    leg = decompose_trade_leg("long", 100.0, 100.0, 102.0, 101.8, 1.0)
    assert leg.survol_polling is None
    assert leg.delai_broker is None


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
    assert result.invalide is False


def test_aggregate_weights_gestion_delay_fields_by_fraction():
    leg1 = TradeLegDecomposition(r_theoretical=1.0, cout_entree=0.1, cout_sortie=0.05, derive_gestion=0.0, survol_polling=0.02, delai_broker=0.03)
    leg2 = TradeLegDecomposition(r_theoretical=2.0, cout_entree=0.1, cout_sortie=0.05, derive_gestion=0.0, survol_polling=0.04, delai_broker=0.01)
    result = aggregate_trade_decomposition([(0.5, leg1), (0.5, leg2)])
    assert result.survol_polling == pytest.approx(0.03)
    assert result.delai_broker == pytest.approx(0.02)


def test_aggregate_component_none_if_any_leg_missing_it():
    leg1 = TradeLegDecomposition(r_theoretical=1.0, cout_entree=0.1, cout_sortie=0.05, derive_gestion=0.0)
    leg2 = TradeLegDecomposition(r_theoretical=2.0, cout_entree=0.1, cout_sortie=None, derive_gestion=None)
    result = aggregate_trade_decomposition([(0.5, leg1), (0.5, leg2)])
    assert result.cout_entree == pytest.approx(0.1)  # présent sur les deux jambes
    assert result.cout_sortie is None  # manquant sur une jambe -> tout le trade est None
    assert result.derive_gestion is None


# ---------------------------------------------------------------------------
# is_cout_sortie_plausible
# ---------------------------------------------------------------------------

def test_plausible_when_cout_sortie_none():
    assert is_cout_sortie_plausible(None, spread=1.0, stop_distance=10.0) is True


def test_plausible_when_spread_none():
    assert is_cout_sortie_plausible(1.0, spread=None, stop_distance=10.0) is True


def test_plausible_when_spread_zero_or_negative():
    assert is_cout_sortie_plausible(1.0, spread=0.0, stop_distance=10.0) is True
    assert is_cout_sortie_plausible(1.0, spread=-1.0, stop_distance=10.0) is True


def test_plausible_within_default_ratio():
    # cout_sortie(R)=0.1, stop=10 -> cout_sortie_price=1.0 ; spread=0.2 -> ratio=5 <= 10
    assert is_cout_sortie_plausible(0.1, spread=0.2, stop_distance=10.0) is True


def test_implausible_beyond_default_ratio():
    # Réplique le cas réel corrompu (trade 14239) : cout_sortie(R)=1.0292,
    # stop_distance=44.52, spread=1.8 -> ratio ~ 25.5 > 10.
    assert is_cout_sortie_plausible(1.0292209943528332, spread=1.8, stop_distance=44.5239654568) is False


def test_plausibility_uses_absolute_value_of_cout_sortie():
    assert is_cout_sortie_plausible(-1.0292209943528332, spread=1.8, stop_distance=44.5239654568) is False


# ---------------------------------------------------------------------------
# Orchestration (DB réelle)
# ---------------------------------------------------------------------------

def _insert_trade_with_partials(db_path, direction="long", entry_prevu=100.0, entry_reel=100.0,
                                 stop_initial=99.0, partials=None, actif="GOLD", source="hypothesis5",
                                 ouvert_at="2026-06-01T00:00:00Z", ferme_at="2026-06-02T00:00:00Z",
                                 r_multiple_total=None):
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, ferme_at, r_multiple_total, statut) "
            "VALUES (NULL, ?, ?, 'demo', ?, 0.01, ?, ?, ?, ?, 10.0, 2.0, ?, ?, ?, 'ferme')",
            (source, actif, direction, entry_prevu, entry_reel, stop_initial, stop_initial,
             ouvert_at, ferme_at, r_multiple_total),
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
    assert result.derive_gestion == pytest.approx(0.0, abs=1e-9)
    assert result.invalide is False  # pas de market_snapshots -> spread inconnu -> jamais invalidé


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


def _insert_trade_with_signal_and_spread(db_path, spread, direction="long", entry_prevu=100.0,
                                          entry_reel=100.0, stop_initial=99.0, partials=None,
                                          actif="GOLD", source="hypothesis5"):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, 'x', '2026-06-01T00:00:00Z', 't', 'signal')"
        ).lastrowid
        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
            "confiance, statut, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 'approuve', '2026-06-01T00:00:00Z')",
            (raw_id, source, actif, direction, entry_prevu, entry_prevu, stop_initial),
        ).lastrowid
        conn.execute(
            "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) "
            "VALUES (?, ?, ?, ?, '2026-06-01T00:00:00Z')",
            (signal_id, entry_prevu, entry_prevu + spread, spread),
        )
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, ferme_at, statut) "
            "VALUES (?, ?, ?, 'demo', ?, 0.01, ?, ?, ?, ?, 10.0, 2.0, "
            "'2026-06-01T00:00:00Z', '2026-06-02T00:00:00Z', 'ferme')",
            (signal_id, source, actif, direction, entry_prevu, entry_reel, stop_initial, stop_initial),
        ).lastrowid
        for fraction, r_atteint, prix_sortie, prix_sortie_reel in partials:
            conn.execute(
                "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, executed_at, prix_sortie_reel) "
                "VALUES (?, 'tp', ?, ?, ?, '2026-06-02T00:00:00Z', ?)",
                (trade_id, fraction, prix_sortie, r_atteint, prix_sortie_reel),
            )
    return trade_id


def test_compute_marks_invalide_when_cout_sortie_implausible_vs_spread(tmp_path):
    # Réplique le cas réel corrompu (trade 14239, 28/08/2026) : confirmation
    # de clôture périmée -> prix_sortie_reel == prix d'entrée.
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_signal_and_spread(
        db_path, spread=1.8, direction="long", entry_prevu=29558.4, entry_reel=29558.4,
        stop_initial=29513.876034543184,
        partials=[(1.0, 1.0292209943528332, 29604.225, 29558.4)],  # prix_sortie_reel = prix d'entree (corrompu)
    )
    result = compute_trade_causal_decomposition(db_path, trade_id)
    assert result is not None
    assert result.invalide is True


def test_compute_not_invalide_when_cout_sortie_plausible_vs_spread(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_signal_and_spread(
        db_path, spread=1.8, direction="short", entry_prevu=53652.3, entry_reel=53652.3,
        stop_initial=53713.3,
        partials=[(1.0, 1.0, 53591.3, 53582.3)],  # cas réel authentique, trade 14249
    )
    result = compute_trade_causal_decomposition(db_path, trade_id)
    assert result is not None
    assert result.invalide is False


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


def test_aggregate_excludes_invalide_rows(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_signal_and_spread(
        db_path, spread=1.8, direction="long", entry_prevu=29558.4, entry_reel=29558.4,
        stop_initial=29513.876034543184, actif="US100", source="hypothesis4",
        partials=[(1.0, 1.0292209943528332, 29604.225, 29558.4)],
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    assert decomposition.invalide is True
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-02T00:00:00Z")

    assert aggregate_by_hypothesis_asset_month(db_path) == []


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


# ---------------------------------------------------------------------------
# Point 4 (29/08/2026) : session_bucket_from_ouvert_at / holding_duration_
# hours / duration_bucket / outcome_label / aggregate_by_dimension /
# classify_cost_vs_strategy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ouvert_at, expected", [
    ("2026-06-01T00:00:00Z", "00h-06hUTC_asie"),
    ("2026-06-01T05:59:00Z", "00h-06hUTC_asie"),
    ("2026-06-01T06:00:00Z", "06h-12hUTC_europe"),
    ("2026-06-01T11:59:00Z", "06h-12hUTC_europe"),
    ("2026-06-01T12:00:00Z", "12h-18hUTC_chevauchement_us"),
    ("2026-06-01T17:59:00Z", "12h-18hUTC_chevauchement_us"),
    ("2026-06-01T18:00:00Z", "18h-24hUTC_fin_us"),
    ("2026-06-01T23:59:00Z", "18h-24hUTC_fin_us"),
])
def test_session_bucket_from_ouvert_at(ouvert_at, expected):
    assert session_bucket_from_ouvert_at(ouvert_at) == expected


def test_session_bucket_rejects_malformed_hour():
    # Garde-fou sur donnee corrompue (meme registre que le ValueError de
    # decompose_trade_leg sur une direction inconnue) - jamais un bucket
    # par defaut silencieux sur une heure hors 0-23.
    with pytest.raises(ValueError):
        session_bucket_from_ouvert_at("2026-06-01T99:00:00Z")


def test_holding_duration_hours():
    assert holding_duration_hours("2026-06-01T00:00:00Z", "2026-06-01T03:30:00Z") == pytest.approx(3.5)


def test_holding_duration_hours_across_days():
    assert holding_duration_hours("2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z") == pytest.approx(24.0)


@pytest.mark.parametrize("hours, expected", [
    (0.0, "<1h"), (0.99, "<1h"),
    (1.0, "1h-4h"), (3.99, "1h-4h"),
    (4.0, "4h-12h"), (11.99, "4h-12h"),
    (12.0, "12h-24h"), (23.99, "12h-24h"),
    (24.0, ">24h"), (100.0, ">24h"),
])
def test_duration_bucket(hours, expected):
    assert duration_bucket(hours) == expected


def test_duration_bucket_rejects_negative():
    with pytest.raises(ValueError):
        duration_bucket(-0.01)


@pytest.mark.parametrize("r_multiple_total, expected", [
    (None, "inconnu"), (1.5, "gagnant"), (-0.8, "perdant"), (0.0, "neutre"),
])
def test_outcome_label(r_multiple_total, expected):
    assert outcome_label(r_multiple_total) == expected


def test_aggregate_by_dimension_rejects_unknown_dimension(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with pytest.raises(ValueError):
        aggregate_by_dimension(db_path, "actif")


def test_aggregate_by_dimension_no_data_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert aggregate_by_dimension(db_path, "outcome") == []


def test_aggregate_by_dimension_outcome(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, direction="long", entry_prevu=100.0, entry_reel=100.1, stop_initial=99.0,
        partials=[(1.0, 1.5, 102.0, 101.8)], source="hypothesis5", r_multiple_total=1.5,
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-02T00:00:00Z")

    rows = aggregate_by_dimension(db_path, "outcome")
    assert len(rows) == 1
    assert rows[0]["source"] == "hypothesis5"
    assert rows[0]["outcome"] == "gagnant"
    assert rows[0]["n"] == 1
    assert rows[0]["cout_entree_moyen"] == pytest.approx(0.1)
    assert rows[0]["cout_sortie_moyen"] == pytest.approx(0.2)


def test_aggregate_by_dimension_session(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, partials=[(1.0, 1.0, 101.0, 101.0)],
        ouvert_at="2026-06-01T14:00:00Z", ferme_at="2026-06-01T15:00:00Z",
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-01T15:00:00Z")

    rows = aggregate_by_dimension(db_path, "session")
    assert len(rows) == 1
    assert rows[0]["session"] == "12h-18hUTC_chevauchement_us"


def test_aggregate_by_dimension_duree(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_partials(
        db_path, partials=[(1.0, 1.0, 101.0, 101.0)],
        ouvert_at="2026-06-01T00:00:00Z", ferme_at="2026-06-01T02:00:00Z",
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-01T02:00:00Z")

    rows = aggregate_by_dimension(db_path, "duree")
    assert len(rows) == 1
    assert rows[0]["duree"] == "1h-4h"


def test_aggregate_by_dimension_excludes_invalide_rows(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _insert_trade_with_signal_and_spread(
        db_path, spread=1.8, direction="long", entry_prevu=29558.4, entry_reel=29558.4,
        stop_initial=29513.876034543184,
        partials=[(1.0, 1.0292209943528332, 29604.225, 29558.4)],
    )
    decomposition = compute_trade_causal_decomposition(db_path, trade_id)
    persist_trade_causal_decomposition(db_path, trade_id, decomposition, "2026-06-02T00:00:00Z")

    assert aggregate_by_dimension(db_path, "outcome") == []


def test_classify_missing_cost_data_is_never_treated_as_zero():
    assert classify_cost_vs_strategy(0.05, None, 0.01) == "donnees_de_cout_incompletes"
    assert classify_cost_vs_strategy(0.05, 0.01, None) == "donnees_de_cout_incompletes"


def test_classify_leak_that_flips_positive_edge_to_zero_or_negative():
    # r_theorique=0.05, fuite totale=0.06 -> r_realise implicite <= 0.
    assert classify_cost_vs_strategy(0.05, 0.04, 0.02) == "fuite_execution_a_investiguer"


def test_classify_leak_at_least_half_of_theoretical_edge_even_without_flip():
    # r_theorique=0.20, fuite totale=0.10 (exactement 50%) -> signalee
    # meme si le signe theorique positif survit.
    assert classify_cost_vs_strategy(0.20, 0.06, 0.04) == "fuite_execution_a_investiguer"


def test_classify_negative_theoretical_edge_without_leak_dominance():
    assert classify_cost_vs_strategy(-0.10, 0.0, 0.0) == "edge_theorique_negatif_question_de_lot_gate_de_puissance"


def test_classify_nothing_to_report():
    assert classify_cost_vs_strategy(0.10, 0.0, 0.0) == "rien_a_signaler"


def test_classify_favorable_cost_never_flagged_as_leak():
    # coûts négatifs (favorables, ex. trade 14240 du 28/08/2026) -> jamais
    # une "fuite" même si l'edge théorique est déjà négatif par ailleurs.
    assert classify_cost_vs_strategy(-0.10, -0.02, -0.01) == "edge_theorique_negatif_question_de_lot_gate_de_puissance"
