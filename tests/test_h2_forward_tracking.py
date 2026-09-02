"""Tests de h2_forward_tracking.py (point 3, 30/08/2026, voir docs/DECISIONS.md)."""

import math

import pytest

from src.db import connection_scope, init_db
from src.h2_forward_tracking import (
    EFFECT_SIZE_MILESTONES,
    H2_CONFIRMED_R,
    H2_CONFIRMED_SIGMA,
    H2_FORWARD_CUTOFF,
    MILESTONE_LOWER_BOUND_POSITIVE_N,
    MIN_N_FOR_NEGATIVE_ALERT,
    Z_95_ONE_SIDED,
    h2_forward_trades,
    summarize_h2_forward,
)


def test_effect_size_milestones_match_lower_bound_formula():
    # Meme derivation que les jalons n=53/121 (arrondi au plus proche, pas
    # un plafond - convention issue de docs/Prompt_Apres_Lookahead_31-08.md
    # point 4c, coincide avec un plafond pour n=53/121 mais pas ici).
    for effect_size, expected_n in EFFECT_SIZE_MILESTONES.items():
        computed_n = round((Z_95_ONE_SIDED * H2_CONFIRMED_SIGMA / effect_size) ** 2)
        assert computed_n == expected_n, (effect_size, computed_n, expected_n)


def _insert_trade(conn, source, actif, r_multiple_total, ouvert_at, statut="ferme"):
    conn.execute(
        "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
        "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
        "pourcentage_risque_applique, ouvert_at, statut, r_multiple_total) "
        "VALUES (NULL, ?, ?, 'demo', 'long', 0.01, 100.0, 99.0, 99.0, 10.0, 2.0, ?, ?, ?)",
        (source, actif, ouvert_at, statut, r_multiple_total),
    )


def test_h2_forward_trades_excludes_pre_cutoff_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.5, "2026-08-30T00:00:00Z")  # avant le cutoff
        _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.3, "2026-08-30T08:00:00Z")  # apres le cutoff
    trades = h2_forward_trades(db_path)
    assert len(trades) == 1
    assert trades[0]["r_multiple_total"] == pytest.approx(0.3)


def test_h2_forward_trades_excludes_other_sources_and_open_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis3_v2", "GOLD", 0.5, "2026-08-31T00:00:00Z")
        _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.5, "2026-08-31T00:00:00Z", statut="ouvert")
    assert h2_forward_trades(db_path) == []


def test_h2_forward_trades_custom_cutoff(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.5, "2026-09-01T00:00:00Z")
    assert h2_forward_trades(db_path, cutoff="2026-09-02T00:00:00Z") == []
    assert len(h2_forward_trades(db_path, cutoff="2026-08-31T00:00:00Z")) == 1


def test_summarize_no_trades_yet(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    result = summarize_h2_forward(db_path)
    assert result["n"] == 0
    assert result["mean_r"] is None
    assert result["lower_bound_95"] is None
    assert result["ecart_vs_confirme"] is None
    assert result["alerte"] is None
    assert result["verdict"] == "aucun_verdict_avant_n53"


def test_summarize_single_trade_no_lower_bound(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.5, "2026-08-31T00:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["n"] == 1
    assert result["mean_r"] == pytest.approx(0.5)
    assert result["lower_bound_95"] is None  # n<2, borne indefinie
    assert result["ecart_vs_confirme"] == pytest.approx(0.5 - H2_CONFIRMED_R)


def test_summarize_negative_alert_triggers_exactly_at_threshold(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for i in range(MIN_N_FOR_NEGATIVE_ALERT):
            _insert_trade(conn, "hypothesis2_v2", "GOLD", -0.2, f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["n"] == MIN_N_FOR_NEGATIVE_ALERT
    assert result["mean_r"] < 0
    assert result["alerte"] is not None
    assert "ALERTE" in result["alerte"]


def test_summarize_no_alert_below_n30_even_if_negative(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for i in range(MIN_N_FOR_NEGATIVE_ALERT - 1):
            _insert_trade(conn, "hypothesis2_v2", "GOLD", -0.2, f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["n"] == MIN_N_FOR_NEGATIVE_ALERT - 1
    assert result["alerte"] is None


def test_summarize_no_alert_when_positive_even_above_n30(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for i in range(35):
            _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.2, f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["alerte"] is None


def test_summarize_verdict_stays_pending_below_milestone(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for i in range(MILESTONE_LOWER_BOUND_POSITIVE_N - 1):
            _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.3, f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["verdict"] == "aucun_verdict_avant_n53"


def test_summarize_verdict_positive_lower_bound_at_milestone(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        # Ecart-type nul (toutes valeurs identiques et positives) -> borne
        # basse == moyenne == positive des que n atteint le jalon.
        for i in range(MILESTONE_LOWER_BOUND_POSITIVE_N):
            _insert_trade(conn, "hypothesis2_v2", "GOLD", 0.3, f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["verdict"] == "borne_basse_positive"


def test_summarize_verdict_non_positive_lower_bound_at_milestone(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        # Alterne des valeurs pour introduire de la variance, moyenne
        # positive mais faible -> borne basse peut rester <= 0.
        for i in range(MILESTONE_LOWER_BOUND_POSITIVE_N):
            r = 2.0 if i % 2 == 0 else -1.9
            _insert_trade(conn, "hypothesis2_v2", "GOLD", r, f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
    result = summarize_h2_forward(db_path)
    assert result["verdict"] == "borne_basse_non_positive"
