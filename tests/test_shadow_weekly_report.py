"""
tests/test_shadow_weekly_report.py — Rapport hebdomadaire shadow
(07/10/2026, étape 4).
"""

from datetime import datetime, timezone

import pytest

from src.evolution_v2_test import TradeRef
from src.shadow_weekly_report import (
    build_hypothesis_report,
    cluster_rejection_rate,
    format_weekly_markdown,
    format_weekly_telegram,
)
from src.simulator_fidelity import PortfolioTrade


def _t(asset, direction, ts, r):
    return TradeRef(asset=asset, direction=direction, entry_time_utc=ts, r_multiple=r)


def test_build_hypothesis_report_no_epoch():
    r = build_hypothesis_report("hypothesis_v3cand", None, [], [], 0.24, datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert r.started_at is None and r.status == "aucune époque shadow écrite"


def test_build_hypothesis_report_computes_means_and_milestones():
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    shadow = [_t("GOLD", "long", f"2026-11-{d:02d}T00:00:00", 1.0) for d in range(1, 11)]
    live = [_t("GOLD", "long", f"2026-11-{d:02d}T00:30:00", -0.5) for d in range(1, 11)]
    r = build_hypothesis_report("hypothesis_v3cand", "2026-10-10T00:00:00+00:00", shadow, live, 0.24, now)
    assert r.n_shadow == 10 and r.n_live_baseline == 10
    assert r.mean_shadow == pytest.approx(1.0) and r.mean_live == pytest.approx(-0.5)
    assert r.milestones_reached == [] and r.next_milestone == 30
    assert r.weeks_elapsed > 8


def test_build_hypothesis_report_milestones_reached_at_30():
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    shadow = [_t("GOLD", "long", f"2026-11-{d:02d}T00:00:00", 0.1) for d in range(1, 31)]
    r = build_hypothesis_report("hypothesis_v3cand", "2026-10-10T00:00:00+00:00", shadow, [], 0.24, now)
    assert r.milestones_reached == [30] and r.next_milestone == 40


def test_cluster_rejection_rate_none_when_empty():
    assert cluster_rejection_rate([]) is None


def test_cluster_rejection_rate_blocks_concurrent_same_cluster_trades():
    def _pt(source, asset, entry, exit_, risk):
        return PortfolioTrade(source, asset, entry, exit_, risk, 1.0)

    pooled = [
        _pt("hypothesis_v3cand", "EURUSD", "2026-11-01T00:00", "2026-11-05T00:00", 20.0),
        _pt("hypothesis2_v3cand", "GBPUSD", "2026-11-01T01:00", "2026-11-05T00:00", 20.0),
        _pt("hypothesis3_v3cand", "USDJPY", "2026-11-01T02:00", "2026-11-05T00:00", 20.0),
        _pt("hypothesis4_v3cand", "CHFJPY", "2026-11-01T03:00", "2026-11-05T00:00", 20.0),
    ]
    rate = cluster_rejection_rate(pooled, provisional_risk_eur=20.0)
    # fx : t1 engaged=0->20 (ok) ; t2 engaged=20->40 (ok) ; t3 engaged=40->60>50 (bloqué) ;
    # t4 engaged=40 (t3 jamais retenu) ->60>50 (bloqué) : 2/4 bloqués.
    assert rate == pytest.approx(0.5)


def test_format_weekly_markdown_contains_key_facts():
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    r1 = build_hypothesis_report("hypothesis_v3cand", "2026-10-10T00:00:00+00:00",
                                 [_t("GOLD", "long", "2026-11-01T00:00:00", 0.1)], [], 0.24, now)
    r2 = build_hypothesis_report("hypothesis2_v3cand", None, [], [], 0.13, now)
    markdown = format_weekly_markdown([r1, r2], "2026-12-20", cluster_rate=0.1)
    assert "hypothesis_v3cand" in markdown and "hypothesis2_v3cand" in markdown
    assert "Aucune époque shadow écrite" in markdown
    assert "10.0%" in markdown


def test_format_weekly_telegram_handles_not_started():
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    r = build_hypothesis_report("hypothesis3_v3cand", None, [], [], 0.13, now)
    message = format_weekly_telegram([r])
    assert "non démarré" in message


def test_format_weekly_telegram_contains_diff_and_milestone():
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)
    shadow = [_t("GOLD", "long", f"2026-11-{d:02d}T00:00:00", 1.0) for d in range(1, 11)]
    r = build_hypothesis_report("hypothesis4_v3cand", "2026-10-10T00:00:00+00:00", shadow, [], 0.10, now)
    message = format_weekly_telegram([r])
    assert "hypothesis4_v3cand" in message and "n=10" in message
