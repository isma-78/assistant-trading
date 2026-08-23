from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.hypothesis5_executor import (
    HYPOTHESIS5_ASSETS,
    _describe_signal,
    run_hypothesis5_loop,
)


@dataclass(frozen=True)
class _FakeSignal:
    direction: str
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float


def test_hypothesis5_assets_matches_other_hypotheses():
    assert set(HYPOTHESIS5_ASSETS) == {
        "US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD",
    }


def test_describe_signal_mentions_ict_concepts_and_tp_levels():
    text = _describe_signal("Hypothèse #5", "GOLD", _FakeSignal("long", 2400.0, 2380.0, 2420.0, 2440.0))
    assert "fractal" in text.lower()
    assert "Fibonacci" in text
    assert "FVG" in text
    assert "GOLD" in text
    assert "2420" in text
    assert "2440" in text
    assert "1R" in text
    assert "2R" in text


def test_run_hypothesis5_loop_forwards_h5_credentials_and_resolution():
    config = MagicMock()
    config.capital_api_key_hypothesis5 = "key5"
    config.capital_identifier_hypothesis5 = "id5"
    config.capital_api_password_hypothesis5 = "pwd5"
    config.capital_account_id_hypothesis5 = "acc5"

    with patch("src.hypothesis5_executor.run_technical_strategy_loop") as mock_loop:
        run_hypothesis5_loop(config, "db.sqlite", interval_seconds=42)

    mock_loop.assert_called_once()
    _, kwargs = mock_loop.call_args
    assert kwargs["source"] == "hypothesis5"
    assert kwargs["resolution"] == "HOUR"
    assert kwargs["api_key"] == "key5"
    assert kwargs["identifier"] == "id5"
    assert kwargs["password"] == "pwd5"
    assert kwargs["account_id"] == "acc5"
    assert kwargs["interval_seconds"] == 42
    assert set(kwargs["assets"]) == set(HYPOTHESIS5_ASSETS)


def test_run_hypothesis5_loop_raises_configerror_when_credentials_missing():
    from src.config import ConfigError

    config = MagicMock()
    config.capital_api_key_hypothesis5 = None
    config.capital_identifier_hypothesis5 = None
    config.capital_api_password_hypothesis5 = None
    config.capital_account_id_hypothesis5 = None

    try:
        run_hypothesis5_loop(config, "db.sqlite")
        assert False, "ConfigError attendue"
    except ConfigError:
        pass
