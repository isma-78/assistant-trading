"""Tests de src/couples_forward_report.py (Partie 4, 08/10/2026)."""

import pytest

from src.couples_forward_report import (
    CoupleReport,
    bootstrap_mean_ci,
    build_couple_report,
    cluster_rejection_rates,
    format_markdown,
)
from src.evolution_v2_test import TradeRef
from src.simulator_fidelity import PortfolioTrade


def _trade(r, day="2026-09-01"):
    return TradeRef("GOLD", "long", f"{day}T10:00:00", r)


# --- bootstrap_mean_ci ---------------------------------------------------

def test_bootstrap_mean_ci_empty():
    assert bootstrap_mean_ci([]) is None


def test_bootstrap_mean_ci_returns_ordered_bounds():
    trades = [_trade(0.5, "2026-09-01"), _trade(-0.3, "2026-09-08"), _trade(1.2, "2026-09-15"),
              _trade(0.1, "2026-09-22")]
    ci = bootstrap_mean_ci(trades, reps=500)
    assert ci is not None
    lo, hi = ci
    assert lo <= hi


def test_bootstrap_mean_ci_deterministic_with_seed():
    trades = [_trade(0.5, "2026-09-01"), _trade(-0.3, "2026-09-08")]
    a = bootstrap_mean_ci(trades, reps=200, seed=7)
    b = bootstrap_mean_ci(trades, reps=200, seed=7)
    assert a == b


# --- build_couple_report ---------------------------------------------------

def test_build_couple_report_empty_sides():
    r = build_couple_report("H1", "GOLD", [], [])
    assert r.n_shadow == 0 and r.n_live == 0
    assert r.mean_shadow is None and r.mean_live is None
    assert r.ci_shadow is None and r.ci_live is None
    assert r.milestones_reached == [] and r.next_milestone == 30


def test_build_couple_report_first_milestone_reached():
    shadow = [_trade(0.1, f"2026-0{1 + i // 4}-{(i % 28) + 1:02d}") for i in range(20)]
    live = [_trade(0.2, f"2026-0{1 + i // 4}-{(i % 28) + 1:02d}") for i in range(10)]
    r = build_couple_report("H1", "GOLD", shadow, live)
    assert r.n_shadow == 20 and r.n_live == 10
    assert r.mean_shadow == pytest.approx(0.1)
    assert r.milestones_reached == [30]
    assert r.next_milestone == 50


def test_build_couple_report_all_milestones_reached():
    shadow = [_trade(0.1, f"2026-{1 + (i // 25):02d}-{(i % 28) + 1:02d}") for i in range(100)]
    r = build_couple_report("H1", "GOLD", shadow, [])
    assert r.milestones_reached == [30, 50, 100]
    assert r.next_milestone is None


def test_build_couple_report_carries_cluster_rejection_rate():
    r = build_couple_report("H1", "GOLD", [], [], cluster_rejection_rate=0.25)
    assert r.cluster_rejection_rate == 0.25


# --- cluster_rejection_rates -------------------------------------------------

def test_cluster_rejection_rates_empty():
    assert cluster_rejection_rates([], 10.0) == {}


def test_cluster_rejection_rates_per_couple():
    # Deux trades du même cluster/fenêtre temporelle qui se chevauchent :
    # le second doit être bloqué si le risque provisoire dépasse le plafond.
    trades = [
        PortfolioTrade("hypothesis_v2_shadow_couples", "GOLD", "2026-09-01T00:00:00", "2026-09-02T00:00:00", 40.0, 0.5),
        PortfolioTrade("hypothesis2_v2_shadow_couples", "GOLD", "2026-09-01T06:00:00", "2026-09-01T12:00:00", 40.0, 0.3),
    ]
    rates = cluster_rejection_rates(trades, provisional_risk_eur=40.0)
    assert set(rates.keys()) == {("hypothesis_v2_shadow_couples", "GOLD"), ("hypothesis2_v2_shadow_couples", "GOLD")}
    assert any(v > 0 for v in rates.values())


# --- format_markdown ---------------------------------------------------------

def test_format_markdown_contains_couple_and_fields():
    report = CoupleReport("H1", "GOLD", 2, 1, 0.1, 0.2, (0.0, 0.3), (0.1, 0.3), [30], 50, 0.1)
    md = format_markdown([report], "2026-10-08")
    assert "H1 x GOLD" in md
    assert "n shadow = 2" in md
    assert "aucune action automatique" in md
