from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.hypothesis3_executor import (
    HYPOTHESIS3_ASSETS,
    _describe_signal,
    run_hypothesis3_loop,
)


@dataclass(frozen=True)
class _FakeSignal:
    direction: str
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float


def test_hypothesis3_assets_matches_hypothesis1():
    # Décision documentée dans docs/HYPOTHESES.md (20/08/2026) : mêmes 8
    # actifs que l'Hypothèse #1, compte totalement séparé.
    # CHFJPY ajoutée le 28/08/2026 (voir docs/DECISIONS.md, point 2).
    assert set(HYPOTHESIS3_ASSETS) == {
        "US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD", "CHFJPY",
    }


def test_describe_signal_mentions_pullback():
    # Refonte L3 (29/08/2026, voir docs/DECISIONS.md) : MA200+Donchian
    # remplacés par le pullback en tendance sur régime structurel.
    text = _describe_signal("Hypothèse #3", "GOLD", _FakeSignal("long", 2400.0, 2380.0, 2420.0, 2440.0))
    assert "pullback" in text.lower()
    assert "GOLD" in text
    assert "2420" in text
    assert "2440" in text
    assert "1R" in text
    assert "2R" in text


def test_run_hypothesis3_loop_forwards_h3_credentials_and_resolution():
    config = MagicMock()
    config.capital_api_key_hypothesis3 = "key3"
    config.capital_identifier_hypothesis3 = "id3"
    config.capital_api_password_hypothesis3 = "pwd3"
    config.capital_account_id_hypothesis3 = "acc3"

    with patch("src.hypothesis3_executor.run_technical_strategy_loop") as mock_loop:
        run_hypothesis3_loop(config, "db.sqlite", interval_seconds=42)

    mock_loop.assert_called_once()
    _, kwargs = mock_loop.call_args
    assert kwargs["source"] == "hypothesis3_v2"  # refonte L3, 29/08/2026 (voir docs/DECISIONS.md)
    assert kwargs["resolution"] == "MINUTE_15"
    assert kwargs["api_key"] == "key3"
    assert kwargs["identifier"] == "id3"
    assert kwargs["password"] == "pwd3"
    assert kwargs["account_id"] == "acc3"
    assert kwargs["interval_seconds"] == 42
    assert set(kwargs["assets"]) == set(HYPOTHESIS3_ASSETS)
    assert "session_gated" not in kwargs  # paramètre retiré, voir docs/DECISIONS.md
    assert kwargs["require_regime_confirmation"] is False  # L3 a son propre regime interne (redondant sinon)


def test_run_hypothesis3_loop_default_startup_offset_is_30s():
    # Échelonnement des 6 process (24/08/2026, voir docs/DECISIONS.md) :
    # executor_loop=0s, trend_executor=10s, H2=20s, H3=30s, H4=40s, H5=50s.
    config = MagicMock()
    with patch("src.hypothesis3_executor.run_technical_strategy_loop") as mock_loop:
        run_hypothesis3_loop(config, "db.sqlite")
    _, kwargs = mock_loop.call_args
    assert kwargs["startup_offset_seconds"] == 30
