from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.db import init_db
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
    # CHFJPY ajoutée le 28/08/2026 (voir docs/DECISIONS.md, point 2).
    assert set(HYPOTHESIS5_ASSETS) == {
        "US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD", "CHFJPY",
    }


def test_describe_signal_mentions_compression_expansion():
    # Refonte L5 (29/08/2026, voir docs/DECISIONS.md) : régime structurel
    # + RSI(14)/50 remplacés par compression -> expansion (Bollinger).
    text = _describe_signal("Hypothèse #5", "GOLD", _FakeSignal("long", 2400.0, 2380.0, None, None))
    assert "compression" in text.lower()
    assert "GOLD" in text
    assert "trailing" in text.lower()


def test_run_hypothesis5_loop_forwards_h5_credentials_and_resolution():
    config = MagicMock()
    config.capital_api_key_hypothesis5 = "key5"
    config.capital_identifier_hypothesis5 = "id5"
    config.capital_api_password_hypothesis5 = "pwd5"
    config.capital_account_id_hypothesis5 = "acc5"

    with patch("src.hypothesis5_executor.run_technical_strategy_loop") as mock_loop, patch("src.hypothesis5_executor.compute_expensive_hours_by_asset", return_value={}):
        run_hypothesis5_loop(config, "db.sqlite", interval_seconds=42)

    mock_loop.assert_called_once()
    _, kwargs = mock_loop.call_args
    assert kwargs["source"] == "hypothesis5_v2"  # refonte L5, 29/08/2026 (voir docs/DECISIONS.md)
    assert kwargs["resolution"] == "HOUR"  # amendement 29/08/2026 (voir docs/DECISIONS.md) : profondeur M15 insuffisante
    assert "session_gated" not in kwargs  # paramètre retiré, voir docs/DECISIONS.md
    assert kwargs.get("require_regime_confirmation", False) is False
    assert kwargs["api_key"] == "key5"
    assert kwargs["identifier"] == "id5"
    assert kwargs["password"] == "pwd5"
    assert kwargs["account_id"] == "acc5"
    assert kwargs["interval_seconds"] == 42
    assert set(kwargs["assets"]) == set(HYPOTHESIS5_ASSETS)


def test_run_hypothesis5_loop_default_startup_offset_is_50s():
    # Échelonnement des 6 process (24/08/2026, voir docs/DECISIONS.md) :
    # executor_loop=0s, trend_executor=10s, H2=20s, H3=30s, H4=40s, H5=50s.
    config = MagicMock()
    with patch("src.hypothesis5_executor.run_technical_strategy_loop") as mock_loop, patch("src.hypothesis5_executor.compute_expensive_hours_by_asset", return_value={}):
        run_hypothesis5_loop(config, "db.sqlite")
    _, kwargs = mock_loop.call_args
    assert kwargs["startup_offset_seconds"] == 50


def test_run_hypothesis5_loop_raises_configerror_when_credentials_missing():
    from src.config import ConfigError

    config = MagicMock()
    config.capital_api_key_hypothesis5 = None
    config.capital_identifier_hypothesis5 = None
    config.capital_api_password_hypothesis5 = None
    config.capital_account_id_hypothesis5 = None

    with patch("src.hypothesis5_executor.compute_expensive_hours_by_asset", return_value={}):
        try:
            run_hypothesis5_loop(config, "db.sqlite")
            assert False, "ConfigError attendue"
        except ConfigError:
            pass


def test_run_hypothesis5_loop_activates_expensive_hours_filter(tmp_path):
    # Point 1 (30/08/2026, voir docs/DECISIONS.md) : H5 est close au
    # test d'information mais reste en demo - le filtre doit rester actif.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    config = MagicMock()
    with patch("src.hypothesis5_executor.run_technical_strategy_loop") as mock_loop, \
         patch("src.hypothesis5_executor.compute_expensive_hours_by_asset", return_value={"GBPUSD": {21}}) as mock_expensive:
        run_hypothesis5_loop(config, db_path)
    mock_expensive.assert_called_once_with(db_path)
    _, kwargs = mock_loop.call_args
    assert kwargs["expensive_hours_by_asset"] == {"GBPUSD": {21}}
