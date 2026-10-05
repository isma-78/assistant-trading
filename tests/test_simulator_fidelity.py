"""A8 (bilan du 05/10/2026) : modèle de fidélité du simulateur."""

import pytest

from src.simulator_fidelity import (
    PortfolioTrade,
    StopRefusalModel,
    apply_cluster_cap,
    measured_refusal_rates,
)


def test_measured_rates_match_bilan_and_small_samples_fall_back_to_overall():
    per_asset, overall = measured_refusal_rates()
    assert overall == pytest.approx(62 / 314)
    assert per_asset["BTCUSD"] == pytest.approx(22 / 48)
    assert per_asset["USDJPY"] == 0.0
    assert "US30" not in per_asset  # 2 demandes seulement
    assert measured_refusal_rates({}, 10) == ({}, 0.0)


def test_refusal_model_is_reproducible_and_uses_default_rate():
    a = StopRefusalModel(seed=1)
    b = StopRefusalModel(seed=1)
    seq_a = [a("BTCUSD", 1.0, 2.0) for _ in range(200)]
    assert seq_a == [b("BTCUSD", 1.0, 2.0) for _ in range(200)]
    assert a.requests == 200 and 0.3 < a.refusals / a.requests < 0.6
    assert a.rate_for("GBPUSD") == pytest.approx(62 / 314)
    never = StopRefusalModel(rates={"X": 0.0}, default_rate=1.0)
    assert never("X", 1, 2) is True and never("Y", 1, 2) is False


def _t(source, asset, start, end, risk=10.0):
    return PortfolioTrade(source, asset, start, end, risk, 1.0)


def test_cluster_cap_blocks_fourth_concurrent_position_in_same_cluster():
    trades = [
        _t("H1", "EURUSD", "2026-01-01T00", "2026-01-05T00"),
        _t("H2", "GBPUSD", "2026-01-01T01", "2026-01-05T00"),
        _t("H3", "USDJPY", "2026-01-01T02", "2026-01-05T00"),
        _t("H4", "CHFJPY", "2026-01-01T03", "2026-01-05T00"),  # 30 + 20 = 50, pas > 50 : passe
        _t("H5", "EURUSD", "2026-01-01T04", "2026-01-05T00"),  # 40 + 20 > 50 : bloqué
        _t("H5", "GOLD", "2026-01-01T04", "2026-01-02T00"),    # autre cluster : passe
        _t("H5", "EURUSD", "2026-01-06T00", "2026-01-07T00"),  # risque libéré : passe
        _t("X", "UNKNOWN", "2026-01-01T05", "2026-01-02T00"),  # hors cluster : passe
    ]
    kept, blocked = apply_cluster_cap(trades, provisional_risk_eur=20.0)
    assert [(b.source, b.asset) for b in blocked] == [("H5", "EURUSD")]
    assert len(kept) == 7
