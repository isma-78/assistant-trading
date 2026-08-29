from datetime import datetime, timedelta, timezone

import pytest

from src.circuit_breaker import (
    CLUSTER_EXPOSURE_CAP_EUR,
    CORRELATION_CLUSTERS,
    DAY_LOSS_THRESHOLD_R,
    DRAWDOWN_FROM_PEAK_THRESHOLD_R,
    WEEK_LOSS_THRESHOLD_R,
    RStats,
    compute_r_stats,
    evaluate_api_error_streak,
    evaluate_breadth_pause,
    evaluate_circuit_breakers,
    evaluate_cluster_exposure_cap,
    evaluate_exposure_cap,
    is_channel_inactive,
)

UTC = timezone.utc


def _ts(days_ago: float) -> str:
    return (datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC) - timedelta(days=days_ago)).isoformat()


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)  # jeudi


# ---------------------------------------------------------------------------
# compute_r_stats
# ---------------------------------------------------------------------------

def test_compute_r_stats_empty_history():
    stats = compute_r_stats([], NOW)
    assert stats == RStats(0.0, 0.0, 0.0, 0.0, 0.0)


def test_compute_r_stats_day_week_cumulative():
    trades = [
        (_ts(0.1), -1.0),   # aujourd'hui
        (_ts(0.5), -1.5),   # aujourd'hui aussi
        (_ts(2), 2.0),      # cette semaine, pas aujourd'hui
        (_ts(20), -3.0),    # ancien, hors semaine
    ]
    stats = compute_r_stats(trades, NOW)
    assert stats.day_r == pytest.approx(-2.5)
    assert stats.week_r == pytest.approx(-0.5)
    # cumulatif total (ordre chronologique) : -3.0, -4.0, -2.0, -3.5
    assert stats.cumulative_r == pytest.approx(-3.5)
    assert stats.peak_r == pytest.approx(0.0)
    assert stats.drawdown_from_peak_r == pytest.approx(-3.5)


def test_compute_r_stats_peak_tracks_running_high():
    trades = [(_ts(3), 5.0), (_ts(2), -2.0), (_ts(1), 1.0)]
    stats = compute_r_stats(trades, NOW)
    # cumulatif : 5.0, 3.0, 4.0 -> peak = 5.0, courant = 4.0
    assert stats.peak_r == pytest.approx(5.0)
    assert stats.cumulative_r == pytest.approx(4.0)
    assert stats.drawdown_from_peak_r == pytest.approx(-1.0)


def test_compute_r_stats_week_boundary_monday_utc():
    # NOW = jeudi 20/08/2026 12:00 UTC -> lundi 17/08/2026 00:00 UTC
    just_before_week = "2026-08-16T23:59:59+00:00"  # dimanche, semaine précédente
    just_after_week = "2026-08-17T00:00:01+00:00"    # lundi, cette semaine
    stats = compute_r_stats([(just_before_week, -9.0), (just_after_week, -1.0)], NOW)
    assert stats.week_r == pytest.approx(-1.0)


def test_compute_r_stats_requires_timezone_aware_now():
    with pytest.raises(ValueError):
        compute_r_stats([], datetime(2026, 8, 20, 12, 0, 0))


def test_compute_r_stats_requires_timezone_aware_trade_timestamp():
    with pytest.raises(ValueError):
        compute_r_stats([("2026-08-20T12:00:00", -1.0)], NOW)  # naive


# ---------------------------------------------------------------------------
# evaluate_circuit_breakers (fail-safe wrapper + logique de déclenchement)
# ---------------------------------------------------------------------------

def test_no_breaker_when_all_within_thresholds():
    trades = [(_ts(0.1), -1.0)]
    status = evaluate_circuit_breakers(trades, NOW, False, False, False)
    assert status.blocked is False
    assert status.active_reasons == []
    assert status.new_triggers == []


def test_day_breaker_new_trigger():
    trades = [(_ts(0.1), -1.0), (_ts(0.2), -1.5)]  # jour = -2.5
    status = evaluate_circuit_breakers(trades, NOW, False, False, False)
    assert status.blocked is True
    assert status.active_reasons == ["day_r"]
    assert status.new_triggers == ["day_r"]
    assert status.r_stats.day_r <= DAY_LOSS_THRESHOLD_R


def test_day_breaker_already_triggered_today_no_new_trigger():
    trades = [(_ts(0.1), -0.1)]  # sous le seuil en direct
    status = evaluate_circuit_breakers(trades, NOW, True, False, False)
    assert status.blocked is True
    assert status.active_reasons == ["day_r"]
    assert status.new_triggers == []  # déjà connu, pas un nouveau déclenchement


def test_week_breaker_new_trigger():
    trades = [(_ts(1), -5.0)]
    status = evaluate_circuit_breakers(trades, NOW, False, False, False)
    assert "week_r" in status.active_reasons
    assert "week_r" in status.new_triggers
    assert status.r_stats.week_r <= WEEK_LOSS_THRESHOLD_R


def test_week_breaker_latched_persists_regardless_of_live_recompute():
    # R en direct au-dessus du seuil, mais latché manuellement -> reste bloqué
    trades = [(_ts(1), -0.5)]
    status = evaluate_circuit_breakers(trades, NOW, False, True, False)
    assert status.blocked is True
    assert status.active_reasons == ["week_r"]
    assert status.new_triggers == []


def test_drawdown_breaker_new_trigger():
    trades = [(_ts(3), 5.0), (_ts(2), -17.0)]  # peak=5, cumulatif=-12 -> drawdown=-17
    status = evaluate_circuit_breakers(trades, NOW, False, False, False)
    assert "drawdown_r" in status.active_reasons
    assert "drawdown_r" in status.new_triggers
    assert status.r_stats.drawdown_from_peak_r <= DRAWDOWN_FROM_PEAK_THRESHOLD_R


def test_drawdown_breaker_latched_persists():
    trades = [(_ts(3), 1.0)]  # drawdown en direct négligeable
    status = evaluate_circuit_breakers(trades, NOW, False, False, True)
    assert status.blocked is True
    assert status.active_reasons == ["drawdown_r"]
    assert status.new_triggers == []


def test_multiple_breakers_simultaneously():
    trades = [(_ts(0.1), -2.0), (_ts(1), -3.0)]  # jour=-2 (seuil pile), semaine=-5 (seuil pile)
    status = evaluate_circuit_breakers(trades, NOW, False, False, False)
    assert set(status.active_reasons) == {"day_r", "week_r"}
    assert set(status.new_triggers) == {"day_r", "week_r"}


def test_evaluate_circuit_breakers_fail_safe_on_internal_error():
    # Timestamp naïf -> compute_r_stats lève ValueError, capturé ici
    status = evaluate_circuit_breakers([("2026-08-20T12:00:00", -1.0)], NOW, False, False, False)
    assert status.blocked is True
    assert status.active_reasons == ["internal_error"]
    assert status.new_triggers == []


# ---------------------------------------------------------------------------
# evaluate_exposure_cap
# ---------------------------------------------------------------------------

def test_exposure_cap_within_limit():
    assert evaluate_exposure_cap(open_risk_eur=20.0, envelope_balance=500.0, new_risk_eur=10.0) is False


def test_exposure_cap_exceeded():
    assert evaluate_exposure_cap(open_risk_eur=45.0, envelope_balance=500.0, new_risk_eur=10.0) is True


def test_exposure_cap_exact_boundary_not_exceeded():
    # 10% de 500 = 50 pile -> pas un dépassement (>, pas >=)
    assert evaluate_exposure_cap(open_risk_eur=40.0, envelope_balance=500.0, new_risk_eur=10.0) is False


def test_exposure_cap_depleted_envelope_always_blocks():
    assert evaluate_exposure_cap(open_risk_eur=0.0, envelope_balance=0.0, new_risk_eur=1.0) is True
    assert evaluate_exposure_cap(open_risk_eur=0.0, envelope_balance=-5.0, new_risk_eur=1.0) is True


# ---------------------------------------------------------------------------
# evaluate_cluster_exposure_cap / CORRELATION_CLUSTERS
# ---------------------------------------------------------------------------

def test_cluster_exposure_cap_within_limit():
    assert evaluate_cluster_exposure_cap(cluster_open_risk_eur=20.0, new_risk_eur=10.0) is False


def test_cluster_exposure_cap_exceeded():
    assert evaluate_cluster_exposure_cap(cluster_open_risk_eur=45.0, new_risk_eur=10.0) is True


def test_cluster_exposure_cap_exact_boundary_not_exceeded():
    assert evaluate_cluster_exposure_cap(cluster_open_risk_eur=40.0, new_risk_eur=10.0) is False


def test_cluster_exposure_cap_default_matches_module_constant():
    assert evaluate_cluster_exposure_cap(cluster_open_risk_eur=CLUSTER_EXPOSURE_CAP_EUR, new_risk_eur=0.01) is True


def test_cluster_exposure_cap_custom_cap_overrides_default():
    assert evaluate_cluster_exposure_cap(cluster_open_risk_eur=90.0, new_risk_eur=5.0, cluster_cap_eur=100.0) is False


def test_correlation_clusters_chfjpy_grouped_with_usdjpy():
    assert CORRELATION_CLUSTERS["CHFJPY"] == CORRELATION_CLUSTERS["USDJPY"]


def test_correlation_clusters_crypto_pair_grouped():
    assert CORRELATION_CLUSTERS["BTCUSD"] == CORRELATION_CLUSTERS["ETHUSD"]


def test_correlation_clusters_gold_standalone():
    gold_cluster = CORRELATION_CLUSTERS["GOLD"]
    assert all(asset == "GOLD" for asset, cluster in CORRELATION_CLUSTERS.items() if cluster == gold_cluster)


# ---------------------------------------------------------------------------
# evaluate_api_error_streak / evaluate_breadth_pause
# ---------------------------------------------------------------------------

def test_api_error_streak_below_threshold():
    assert evaluate_api_error_streak(2) is False


def test_api_error_streak_at_threshold():
    assert evaluate_api_error_streak(3) is True


def test_api_error_streak_above_threshold():
    assert evaluate_api_error_streak(4) is True


def test_breadth_pause_below_threshold():
    assert evaluate_breadth_pause(4) is False


def test_breadth_pause_at_threshold():
    assert evaluate_breadth_pause(5) is True


# ---------------------------------------------------------------------------
# is_channel_inactive
# ---------------------------------------------------------------------------

def test_channel_inactive_none_never_alerts():
    assert is_channel_inactive(None, NOW) is False


def test_channel_inactive_recent_message_no_alert():
    assert is_channel_inactive(NOW - timedelta(days=1), NOW) is False


def test_channel_inactive_seven_days_alerts():
    assert is_channel_inactive(NOW - timedelta(days=7), NOW) is True


def test_channel_inactive_just_under_threshold_no_alert():
    assert is_channel_inactive(NOW - timedelta(days=6, hours=23), NOW) is False


def test_channel_inactive_requires_timezone_aware():
    with pytest.raises(ValueError):
        is_channel_inactive(datetime(2026, 8, 1), NOW)
    with pytest.raises(ValueError):
        is_channel_inactive(NOW - timedelta(days=8), datetime(2026, 8, 20))
