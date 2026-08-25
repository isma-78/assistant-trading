from datetime import datetime, timedelta, timezone

import pytest

from src.db import connection_scope, init_db
from src.envelope_store import load_or_create_envelope, persist_trade_result
from src.capital_manager import CapitalManager, apply_trade_result
from src.metrics import (
    AssetMetrics,
    compute_asset_metrics,
    compute_period_pnl,
    get_aggregate_period_pnl,
    get_all_asset_keys,
    get_all_asset_metrics,
    get_asset_metrics,
    get_asset_period_pnl,
    get_reserve_total,
    get_total_demo_capital,
    get_trade_counts_by_period,
    get_trade_pnl_movements,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)  # jeudi


# ---------------------------------------------------------------------------
# compute_asset_metrics (calcul pur)
# ---------------------------------------------------------------------------

def test_compute_asset_metrics_no_trades():
    m = compute_asset_metrics("GOLD", "stationx", [])
    assert m == AssetMetrics("GOLD", "stationx", 0, None, None, None, 0.0, 0.0)


def test_compute_asset_metrics_mixed_trades():
    m = compute_asset_metrics("GOLD", "stationx", [2.0, -1.0, 1.0, -0.5])
    assert m.nb_trades == 4
    assert m.esperance_r == pytest.approx(0.375)
    assert m.profit_factor == pytest.approx(3.0 / 1.5)
    assert m.taux_reussite_indicatif == pytest.approx(50.0)


def test_compute_asset_metrics_only_wins_profit_factor_infinite():
    m = compute_asset_metrics("GOLD", "stationx", [1.0, 2.0])
    assert m.profit_factor == float("inf")
    assert m.taux_reussite_indicatif == pytest.approx(100.0)


def test_compute_asset_metrics_only_losses_profit_factor_zero():
    m = compute_asset_metrics("GOLD", "stationx", [-1.0, -0.5])
    assert m.profit_factor == pytest.approx(0.0)
    assert m.taux_reussite_indicatif == pytest.approx(0.0)


def test_compute_asset_metrics_all_breakeven_profit_factor_none():
    m = compute_asset_metrics("GOLD", "stationx", [0.0, 0.0])
    assert m.profit_factor is None
    assert m.taux_reussite_indicatif == pytest.approx(0.0)  # R=0 n'est pas un gain (r > 0 strict)


def test_compute_asset_metrics_drawdown_current_and_max():
    # cumulatif : 5, 3, 4, -2, 0 -> peak suit 5,5,5,5,5 ; creux successifs : 0,-2,-1,-7,-5
    m = compute_asset_metrics("GOLD", "stationx", [5.0, -2.0, 1.0, -6.0, 2.0])
    assert m.drawdown_max_r == pytest.approx(-7.0)
    assert m.drawdown_courant_r == pytest.approx(-5.0)


def test_compute_asset_metrics_no_drawdown_when_always_new_high():
    m = compute_asset_metrics("GOLD", "stationx", [1.0, 1.0, 1.0])
    assert m.drawdown_courant_r == pytest.approx(0.0)
    assert m.drawdown_max_r == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_period_pnl (calcul pur)
# ---------------------------------------------------------------------------

def test_compute_period_pnl_empty():
    assert compute_period_pnl([], NOW) == (0.0, 0.0, 0.0)


def test_compute_period_pnl_buckets_correctly():
    movements = [
        ((NOW - timedelta(hours=1)).isoformat(), 10.0),   # cette semaine, ce mois
        ((NOW - timedelta(days=10)).isoformat(), -5.0),   # ce mois (10/08), pas cette semaine
        ("2026-07-01T00:00:00+00:00", 100.0),             # ni ce mois ni cette semaine, mais depuis le début
    ]
    week, month, all_time = compute_period_pnl(movements, NOW)
    assert week == pytest.approx(10.0)
    assert month == pytest.approx(5.0)
    assert all_time == pytest.approx(105.0)


def test_compute_period_pnl_week_boundary_monday_utc():
    just_before = "2026-08-16T23:59:59+00:00"  # dimanche, semaine précédente
    just_after = "2026-08-17T00:00:01+00:00"    # lundi, cette semaine
    week, _, _ = compute_period_pnl([(just_before, -9.0), (just_after, 3.0)], NOW)
    assert week == pytest.approx(3.0)


def test_compute_period_pnl_requires_timezone_aware_now():
    with pytest.raises(ValueError):
        compute_period_pnl([], datetime(2026, 8, 20))


def test_compute_period_pnl_requires_timezone_aware_movement_timestamp():
    with pytest.raises(ValueError):
        compute_period_pnl([("2026-08-20T12:00:00", 1.0)], NOW)


# ---------------------------------------------------------------------------
# Orchestration I/O (DB SQLite temporaire réelle)
# ---------------------------------------------------------------------------

def _close_trade(db_path, actif, source, r_multiple, ferme_at, cloture_reason=None, anomalie_technique=None):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, ferme_at, r_multiple_total, statut, cloture_reason, anomalie_technique) "
            "VALUES (?, ?, 'demo', 'long', 0.01, 1.0, 1.0, 10.0, 2.0, ?, ?, ?, 'ferme', ?, ?)",
            (source, actif, ferme_at, ferme_at, r_multiple, cloture_reason, anomalie_technique),
        )


def test_get_all_asset_keys_reflects_created_envelopes(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis")

    keys = get_all_asset_keys(db_path)
    assert set(keys) == {("GOLD", "stationx"), ("EURUSD", "hypothesis")}


def test_get_asset_metrics_reads_closed_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "GOLD", "stationx", 2.0, "2026-08-18T00:00:00+00:00")
    _close_trade(db_path, "GOLD", "stationx", -1.0, "2026-08-19T00:00:00+00:00")
    _close_trade(db_path, "GOLD", "hypothesis", 5.0, "2026-08-19T00:00:00+00:00")  # autre source, ignoré

    m = get_asset_metrics(db_path, "GOLD", "stationx")
    assert m.nb_trades == 2
    assert m.esperance_r == pytest.approx(0.5)


def test_get_asset_metrics_excludes_stop_urgence_closures(tmp_path):
    # §7.2/§7.1, 20/08/2026 (voir docs/DECISIONS.md) : un arrêt d'urgence
    # sort à un prix arbitraire, jamais représentatif du calibrage
    # stop/TP/trailing de la stratégie — exclu des stats en R, pas du P&L
    # en euros (envelope_ledger, non testé ici, non filtré ailleurs).
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "GOLD", "stationx", 2.0, "2026-08-18T00:00:00+00:00")
    _close_trade(db_path, "GOLD", "stationx", -100.0, "2026-08-19T00:00:00+00:00", cloture_reason="stop_urgence")

    m = get_asset_metrics(db_path, "GOLD", "stationx")
    assert m.nb_trades == 1
    assert m.esperance_r == pytest.approx(2.0)


def test_get_asset_metrics_excludes_trades_flagged_anomalie_technique(tmp_path):
    # 25/08/2026 (voir docs/DECISIONS.md) : positions H3/ETHUSD simultanées
    # dues à une fenêtre de course dans le garde-fou anti-doublon —
    # l'OUVERTURE de ces trades résulte d'un bug, pas d'un signal de
    # stratégie réel. Exclu de l'espérance/l'éligibilité §2.4, même
    # patron que l'exclusion stop_urgence ci-dessus (P&L réel non filtré
    # ailleurs, ex. circuit_breaker_store, non testé ici).
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "ETHUSD", "hypothesis3", 2.0, "2026-08-21T07:17:38+00:00")
    _close_trade(
        db_path, "ETHUSD", "hypothesis3", 2.5, "2026-08-21T07:18:09+00:00",
        anomalie_technique="Position simultanée non bloquée (garde-fou anti-doublon, corrigé le 25/08/2026)",
    )

    m = get_asset_metrics(db_path, "ETHUSD", "hypothesis3")
    assert m.nb_trades == 1
    assert m.esperance_r == pytest.approx(2.0)


def test_get_all_asset_metrics_covers_every_envelope(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis")
    _close_trade(db_path, "GOLD", "stationx", 1.0, "2026-08-19T00:00:00+00:00")

    results = get_all_asset_metrics(db_path)
    by_key = {(m.actif, m.source): m for m in results}
    assert by_key[("GOLD", "stationx")].nb_trades == 1
    assert by_key[("EURUSD", "hypothesis")].nb_trades == 0


def _win_trade_result(db_path, actif, source, pnl):
    envelope_id, manager = load_or_create_envelope(db_path, actif, "demo", 500.0, source=source)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, ?, 'demo', 'long', 0.01, 1.0, 1.0, 10.0, 2.0, '2026-08-19T00:00:00Z', 'ferme')",
            (source, actif),
        ).lastrowid
    balance_before = manager.balance
    reserve_share, reserve_after = apply_trade_result(manager, pnl, reserve_total_before=0.0)
    persist_trade_result(db_path, envelope_id, manager, trade_id, balance_before, reserve_share, reserve_after)


def test_get_trade_pnl_movements_reflects_envelope_share_not_gross_pnl(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _win_trade_result(db_path, "GOLD", "stationx", pnl=20.0)  # enveloppe créditée de 10€ (50%), pas 20€

    movements = get_trade_pnl_movements(db_path, "GOLD", "stationx")
    assert len(movements) == 1
    assert movements[0][1] == pytest.approx(10.0)


def test_get_asset_period_pnl_end_to_end(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _win_trade_result(db_path, "GOLD", "stationx", pnl=20.0)
    week, month, all_time = get_asset_period_pnl(db_path, "GOLD", "stationx", NOW)
    assert all_time == pytest.approx(10.0)


def test_get_aggregate_period_pnl_sums_across_assets(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _win_trade_result(db_path, "GOLD", "stationx", pnl=20.0)
    _win_trade_result(db_path, "EURUSD", "hypothesis", pnl=10.0)
    _, _, all_time = get_aggregate_period_pnl(db_path, NOW)
    assert all_time == pytest.approx(15.0)  # 10 + 5


def test_get_reserve_total_zero_when_no_movement(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_reserve_total(db_path) == 0.0


def test_get_reserve_total_reflects_latest(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _win_trade_result(db_path, "GOLD", "stationx", pnl=20.0)
    assert get_reserve_total(db_path) == pytest.approx(10.0)


def test_get_total_demo_capital_sums_envelopes(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis")
    assert get_total_demo_capital(db_path) == pytest.approx(1000.0)


def test_get_trade_counts_by_period(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "GOLD", "stationx", 1.0, NOW.isoformat())              # cette semaine
    _close_trade(db_path, "GOLD", "stationx", 1.0, "2026-08-01T00:00:00+00:00")  # ce mois, pas cette semaine
    _close_trade(db_path, "GOLD", "stationx", 1.0, "2026-01-01T00:00:00+00:00")  # ni l'un ni l'autre

    counts = get_trade_counts_by_period(db_path, NOW)
    entry = counts[("GOLD", "stationx")]
    assert entry == {"semaine": 1, "mois": 2, "total": 3}


def test_get_trade_counts_by_period_requires_timezone_aware_now(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with pytest.raises(ValueError):
        get_trade_counts_by_period(db_path, datetime(2026, 8, 20))
