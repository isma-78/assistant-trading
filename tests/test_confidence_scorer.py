import pytest

from src.confidence_scorer import (
    PHASE_A_MIN_TRADES,
    PHASE_B_MIN_TRADES,
    SPREAD_MEDIAN_MAX_RATIO,
    ConfidenceScore,
    EligibilityCheck,
    check_esperance_positive,
    check_min_size_compatible,
    check_min_trades,
    check_spread_condition,
    compute_all_confidence_scores,
    compute_confidence_score,
    compute_sample_factor,
    compute_score,
    compute_stability_factor,
    evaluate_confidence,
    get_capital_courant,
    get_median_spread_ratio,
    get_median_stop_distance,
)
from src.db import connection_scope, init_db
from src.envelope_store import load_or_create_envelope
from src.metrics import AssetMetrics
from src.risk_engine import AssetSpec

GOLD_SPEC = AssetSpec(symbol="GOLD", min_units=0.01, pip_value_per_unit=0.8643)


# ---------------------------------------------------------------------------
# compute_sample_factor / compute_stability_factor / compute_score
# ---------------------------------------------------------------------------

def test_compute_sample_factor_zero_trades():
    assert compute_sample_factor(0) == 0.0


def test_compute_sample_factor_negative_defensive():
    assert compute_sample_factor(-5) == 0.0


def test_compute_sample_factor_below_cap():
    assert compute_sample_factor(25) == pytest.approx((25 / 50) ** 0.5)


def test_compute_sample_factor_capped_at_one():
    assert compute_sample_factor(200) == 1.0


def test_compute_sample_factor_exactly_fifty():
    assert compute_sample_factor(50) == pytest.approx(1.0)


def test_compute_stability_factor_no_drawdown():
    assert compute_stability_factor(0.0) == 1.0


def test_compute_stability_factor_partial():
    # |drawdown_max_r| = 3, risk_percent = 2 -> drawdown_% approx = 6 -> 1 - 6/20 = 0.7
    assert compute_stability_factor(-3.0, risk_percent=2.0) == pytest.approx(0.7)


def test_compute_stability_factor_floor_zero():
    # drawdown énorme -> ne devient jamais négatif
    assert compute_stability_factor(-500.0, risk_percent=2.0) == 0.0


def test_compute_score():
    assert compute_score(0.5, 0.8, 0.9) == pytest.approx(0.36)


# ---------------------------------------------------------------------------
# check_* (conditions éliminatoires, calcul pur)
# ---------------------------------------------------------------------------

def test_check_min_trades_insuffisant():
    ok, detail, phase = check_min_trades(19)
    assert not ok
    assert phase == "insuffisant"


def test_check_min_trades_phase_a():
    ok, detail, phase = check_min_trades(PHASE_A_MIN_TRADES)
    assert ok
    assert phase == "A"


def test_check_min_trades_phase_b():
    ok, detail, phase = check_min_trades(PHASE_B_MIN_TRADES)
    assert ok
    assert phase == "B"


def test_check_esperance_positive_none():
    ok, detail = check_esperance_positive(None)
    assert not ok
    assert "indisponible" in detail


def test_check_esperance_positive_negative():
    ok, detail = check_esperance_positive(-0.1)
    assert not ok


def test_check_esperance_positive_zero():
    ok, detail = check_esperance_positive(0.0)
    assert not ok


def test_check_esperance_positive_true():
    ok, detail = check_esperance_positive(0.3)
    assert ok


def test_check_min_size_compatible_no_asset_spec():
    ok, detail = check_min_size_compatible(500.0, 3.0, None)
    assert not ok
    assert "liste blanche" in detail


def test_check_min_size_compatible_no_envelope():
    ok, detail = check_min_size_compatible(None, 3.0, GOLD_SPEC)
    assert not ok
    assert "introuvable" in detail


def test_check_min_size_compatible_no_stop_distance():
    ok, detail = check_min_size_compatible(500.0, None, GOLD_SPEC)
    assert not ok


def test_check_min_size_compatible_zero_stop_distance():
    ok, detail = check_min_size_compatible(500.0, 0.0, GOLD_SPEC)
    assert not ok


def test_check_min_size_compatible_true():
    # risk_amount = 500 * 2% = 10€ ; raw_units = 10 / (3.0 * 0.8643) = 3.856... >= 0.01
    ok, detail = check_min_size_compatible(500.0, 3.0, GOLD_SPEC)
    assert ok


def test_check_min_size_compatible_false_when_below_minimum():
    tiny_spec = AssetSpec(symbol="X", min_units=1000.0, pip_value_per_unit=0.8643)
    ok, detail = check_min_size_compatible(500.0, 3.0, tiny_spec)
    assert not ok


def test_check_spread_condition_no_data():
    ok, detail = check_spread_condition(None)
    assert not ok
    assert "indisponible" in detail


def test_check_spread_condition_below_threshold():
    ok, detail = check_spread_condition(0.05)
    assert ok


def test_check_spread_condition_at_threshold_not_satisfied():
    ok, detail = check_spread_condition(SPREAD_MEDIAN_MAX_RATIO)
    assert not ok


def test_check_spread_condition_above_threshold():
    ok, detail = check_spread_condition(0.5)
    assert not ok


# ---------------------------------------------------------------------------
# evaluate_confidence (assemblage pur)
# ---------------------------------------------------------------------------

def _metrics(nb_trades, esperance_r, drawdown_max_r):
    return AssetMetrics(
        actif="GOLD", source="stationx", nb_trades=nb_trades,
        esperance_r=esperance_r, profit_factor=1.5, taux_reussite_indicatif=50.0,
        drawdown_courant_r=drawdown_max_r, drawdown_max_r=drawdown_max_r,
    )


def test_evaluate_confidence_ineligible_insufficient_trades_no_score():
    m = _metrics(5, 0.3, -1.0)
    result = evaluate_confidence(m, 500.0, 3.0, 0.05, GOLD_SPEC)
    assert not result.eligible
    assert result.score is None
    assert result.phase == "insuffisant"
    assert len(result.checks) == 4


def test_evaluate_confidence_eligible_with_score():
    m = _metrics(60, 0.4, -2.0)
    result = evaluate_confidence(m, 500.0, 3.0, 0.05, GOLD_SPEC, risk_percent=2.0)
    assert result.eligible
    assert result.phase == "B"
    sample_factor = compute_sample_factor(60)
    stability_factor = compute_stability_factor(-2.0, 2.0)
    assert result.score == pytest.approx(compute_score(0.4, sample_factor, stability_factor))


def test_evaluate_confidence_missing_spread_blocks_eligibility_even_with_good_metrics():
    m = _metrics(60, 0.4, -1.0)
    result = evaluate_confidence(m, 500.0, 3.0, None, GOLD_SPEC)
    assert not result.eligible
    assert result.score is None
    spread_check = next(c for c in result.checks if c.condition == "spread_median")
    assert not spread_check.satisfied


def test_evaluate_confidence_eligible_flag_false_when_only_size_condition_fails():
    m = _metrics(60, 0.4, -1.0)
    tiny_spec = AssetSpec(symbol="GOLD", min_units=1000.0, pip_value_per_unit=0.8643)
    result = evaluate_confidence(m, 500.0, 3.0, 0.05, tiny_spec)
    assert not result.eligible
    assert result.score is None


def test_confidence_score_dataclass_defaults_checks_empty_list():
    score = ConfidenceScore(
        actif="GOLD", source="stationx", nb_trades=0, esperance_r=None,
        facteur_echantillon=0.0, facteur_stabilite=1.0, score=None,
        eligible=False, phase="insuffisant",
    )
    assert score.checks == []


def test_eligibility_check_dataclass_fields():
    c = EligibilityCheck("nb_trades", True, "20 trades")
    assert c.condition == "nb_trades"
    assert c.satisfied
    assert c.detail == "20 trades"


# ---------------------------------------------------------------------------
# Orchestration I/O (DB SQLite temporaire réelle)
# ---------------------------------------------------------------------------

def _insert_closed_trade(
    db_path, actif, source, r_multiple, ferme_at,
    prix_entree_reel=None, stop_loss_initial=1.0, signal_id=None,
):
    with connection_scope(db_path) as conn:
        return conn.execute(
            "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, ferme_at, r_multiple_total, statut) "
            "VALUES (?, ?, ?, 'demo', 'long', 0.01, ?, ?, 1.0, 10.0, 2.0, ?, ?, ?, 'ferme')",
            (signal_id, source, actif, prix_entree_reel, stop_loss_initial, ferme_at, ferme_at, r_multiple),
        ).lastrowid


_raw_msg_counter = [0]


def _insert_signal(db_path, actif, source):
    _raw_msg_counter[0] += 1
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (?, 'chan', '2026-08-19T00:00:00+00:00', 'x', 'signal')",
            (_raw_msg_counter[0],),
        ).lastrowid
        return conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, created_at) "
            "VALUES (?, ?, ?, 'long', '2026-08-19T00:00:00+00:00')",
            (raw_id, source, actif),
        ).lastrowid


def _insert_market_snapshot(db_path, signal_id, spread):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) "
            "VALUES (?, 1.0, 1.0, ?, '2026-08-19T00:00:00+00:00')",
            (signal_id, spread),
        )


def test_get_capital_courant_found(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    assert get_capital_courant(db_path, "GOLD", "stationx") == pytest.approx(500.0)


def test_get_capital_courant_missing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_capital_courant(db_path, "GOLD", "stationx") is None


def test_get_median_stop_distance_no_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_median_stop_distance(db_path, "GOLD", "stationx") is None


def test_get_median_stop_distance_computed(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_closed_trade(db_path, "GOLD", "stationx", 1.0, "2026-08-18T00:00:00+00:00",
                          prix_entree_reel=100.0, stop_loss_initial=97.0)   # distance 3
    _insert_closed_trade(db_path, "GOLD", "stationx", -1.0, "2026-08-19T00:00:00+00:00",
                          prix_entree_reel=100.0, stop_loss_initial=95.0)   # distance 5
    _insert_closed_trade(db_path, "GOLD", "hypothesis", 1.0, "2026-08-19T00:00:00+00:00",
                          prix_entree_reel=100.0, stop_loss_initial=50.0)   # autre source, ignoré
    assert get_median_stop_distance(db_path, "GOLD", "stationx") == pytest.approx(4.0)


def test_get_median_stop_distance_ignores_null_prices(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_closed_trade(db_path, "GOLD", "stationx", 1.0, "2026-08-18T00:00:00+00:00")
    assert get_median_stop_distance(db_path, "GOLD", "stationx") is None


def test_get_median_spread_ratio_no_data(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    assert get_median_spread_ratio(db_path, "GOLD", "stationx") is None


def test_get_median_spread_ratio_computed(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    sig1 = _insert_signal(db_path, "GOLD", "stationx")
    _insert_closed_trade(db_path, "GOLD", "stationx", 1.0, "2026-08-18T00:00:00+00:00",
                          prix_entree_reel=100.0, stop_loss_initial=98.0, signal_id=sig1)  # distance 2
    _insert_market_snapshot(db_path, sig1, spread=0.2)  # ratio 0.1

    sig2 = _insert_signal(db_path, "GOLD", "stationx")
    _insert_closed_trade(db_path, "GOLD", "stationx", -1.0, "2026-08-19T00:00:00+00:00",
                          prix_entree_reel=100.0, stop_loss_initial=90.0, signal_id=sig2)  # distance 10
    _insert_market_snapshot(db_path, sig2, spread=2.0)  # ratio 0.2

    assert get_median_spread_ratio(db_path, "GOLD", "stationx") == pytest.approx(0.15)


def test_get_median_spread_ratio_filters_by_source(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    sig = _insert_signal(db_path, "GOLD", "hypothesis")
    _insert_closed_trade(db_path, "GOLD", "hypothesis", 1.0, "2026-08-18T00:00:00+00:00",
                          prix_entree_reel=100.0, stop_loss_initial=98.0, signal_id=sig)
    _insert_market_snapshot(db_path, sig, spread=0.2)
    assert get_median_spread_ratio(db_path, "GOLD", "stationx") is None


def test_compute_confidence_score_end_to_end_missing_data_stays_ineligible(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    for i in range(25):
        _insert_closed_trade(db_path, "GOLD", "stationx", 0.5, f"2026-08-{(i % 27) + 1:02d}T00:00:00+00:00",
                              prix_entree_reel=100.0, stop_loss_initial=97.0)

    result = compute_confidence_score(db_path, "GOLD", "stationx")
    assert result.nb_trades == 25
    assert result.phase == "A"
    # espérance > 0 et taille compatible, mais spread jamais alimenté -> non éligible
    assert not result.eligible
    assert result.score is None


def test_compute_all_confidence_scores_orders_eligible_first_by_score(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    load_or_create_envelope(db_path, "EURUSD", "demo", 500.0, source="hypothesis")

    for i in range(3):
        _insert_closed_trade(db_path, "EURUSD", "hypothesis", 0.2, f"2026-08-{i + 1:02d}T00:00:00+00:00",
                              prix_entree_reel=1.1, stop_loss_initial=1.09)

    scores = compute_all_confidence_scores(db_path)
    assert {s.actif for s in scores} == {"GOLD", "EURUSD"}
    # aucun n'est éligible (spread jamais alimenté, ou trop peu de trades) -> triés par nb_trades décroissant
    assert not any(s.eligible for s in scores)
    assert scores[0].actif == "EURUSD"
    assert scores[0].nb_trades == 3
    assert scores[1].nb_trades == 0
