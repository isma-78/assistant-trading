from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.hypothesis2_executor import (
    HYPOTHESIS2_ASSETS,
    _describe_signal,
    run_hypothesis2_loop,
)


@dataclass(frozen=True)
class _FakeSignal:
    direction: str
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float


def test_hypothesis2_assets_matches_hypothesis1_and_3():
    # CHFJPY ajoutée le 28/08/2026 (voir docs/DECISIONS.md, point 2).
    assert set(HYPOTHESIS2_ASSETS) == {
        "US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD", "CHFJPY",
    }


def test_describe_signal_mentions_confluence_multi_timeframe():
    # Refonte L2 (29/08/2026, voir docs/DECISIONS.md) : ICT/Fibonacci/FVG
    # remplacés par la confluence multi-timeframe EMA/Ichimoku/RSI.
    text = _describe_signal("Hypothèse #2", "GOLD", _FakeSignal("long", 2400.0, 2380.0, 2420.0, 2440.0))
    assert "multi-timeframe" in text.lower() or "ichimoku" in text.lower()
    assert "GOLD" in text
    assert "2420" in text
    assert "2440" in text
    assert "1R" in text
    assert "2R" in text


def test_run_hypothesis2_loop_forwards_h2_credentials_and_resolution():
    config = MagicMock()
    config.capital_api_key_hypothesis2 = "key2"
    config.capital_identifier_hypothesis2 = "id2"
    config.capital_api_password_hypothesis2 = "pwd2"
    config.capital_account_id_hypothesis2 = "acc2"

    with patch("src.hypothesis2_executor.run_technical_strategy_loop") as mock_loop:
        run_hypothesis2_loop(config, "db.sqlite", interval_seconds=42)

    mock_loop.assert_called_once()
    _, kwargs = mock_loop.call_args
    assert kwargs["source"] == "hypothesis2_v2"  # refonte L2, 29/08/2026 (voir docs/DECISIONS.md)
    assert kwargs["resolution"] == "MINUTE_15"
    assert kwargs["extra_resolutions"] == ["HOUR", "HOUR_4"]  # confluence multi-timeframe L2
    assert "session_gated" not in kwargs  # paramètre retiré, voir docs/DECISIONS.md
    assert kwargs.get("require_regime_confirmation", False) is False
    assert kwargs["api_key"] == "key2"
    assert kwargs["identifier"] == "id2"
    assert kwargs["password"] == "pwd2"
    assert kwargs["account_id"] == "acc2"
    assert kwargs["interval_seconds"] == 42
    assert set(kwargs["assets"]) == set(HYPOTHESIS2_ASSETS)


def test_run_hypothesis2_loop_default_startup_offset_is_20s():
    # Échelonnement des 6 process (24/08/2026, voir docs/DECISIONS.md) :
    # executor_loop=0s, trend_executor=10s, H2=20s, H3=30s, H4=40s, H5=50s.
    config = MagicMock()
    with patch("src.hypothesis2_executor.run_technical_strategy_loop") as mock_loop:
        run_hypothesis2_loop(config, "db.sqlite")
    _, kwargs = mock_loop.call_args
    assert kwargs["startup_offset_seconds"] == 20


def test_run_hypothesis2_loop_raises_configerror_when_credentials_missing():
    from src.config import ConfigError

    config = MagicMock()
    config.capital_api_key_hypothesis2 = None
    config.capital_identifier_hypothesis2 = None
    config.capital_api_password_hypothesis2 = None
    config.capital_account_id_hypothesis2 = None

    try:
        run_hypothesis2_loop(config, "db.sqlite")
        assert False, "ConfigError attendue"
    except ConfigError:
        pass
