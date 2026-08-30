from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.db import init_db
from src.hypothesis4_executor import (
    HYPOTHESIS4_ASSETS,
    _describe_signal,
    run_hypothesis4_loop,
)


@dataclass(frozen=True)
class _FakeSignal:
    direction: str
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float


def test_hypothesis4_assets_matches_hypothesis1_2_and_3():
    # CHFJPY ajoutée le 28/08/2026 (voir docs/DECISIONS.md, point 2).
    assert set(HYPOTHESIS4_ASSETS) == {
        "US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD", "CHFJPY",
    }


def test_describe_signal_mentions_divergence_and_tp_levels():
    # Refonte L4 (29/08/2026, voir docs/DECISIONS.md) : Bollinger/take_profit
    # remplacés par la divergence RSI/OBV et la structure TP1/TP2 standard.
    text = _describe_signal("Hypothèse #4", "GOLD", _FakeSignal("long", 2400.0, 2380.0, 2410.0, 2420.0))
    assert "divergence" in text.lower()
    assert "GOLD" in text
    assert "2410.0" in text and "2420.0" in text  # TP1/TP2 mentionnés


def test_run_hypothesis4_loop_forwards_h4_credentials_and_resolution():
    config = MagicMock()
    config.capital_api_key_hypothesis4 = "key4"
    config.capital_identifier_hypothesis4 = "id4"
    config.capital_api_password_hypothesis4 = "pwd4"
    config.capital_account_id_hypothesis4 = "acc4"

    with patch("src.hypothesis4_executor.run_technical_strategy_loop") as mock_loop, patch("src.hypothesis4_executor.compute_expensive_hours_by_asset", return_value={}):
        run_hypothesis4_loop(config, "db.sqlite", interval_seconds=42)

    mock_loop.assert_called_once()
    _, kwargs = mock_loop.call_args
    assert kwargs["source"] == "hypothesis4_v2"  # refonte L4, 29/08/2026 (voir docs/DECISIONS.md)
    assert kwargs["resolution"] == "HOUR"  # amendement 29/08/2026 (voir docs/DECISIONS.md) : profondeur M15 insuffisante
    assert "session_gated" not in kwargs  # paramètre retiré, voir docs/DECISIONS.md
    assert kwargs["require_regime_confirmation"] is False  # L4 n'a aucun filtre de régime (pré-enregistrement)
    assert kwargs["api_key"] == "key4"
    assert kwargs["identifier"] == "id4"
    assert kwargs["password"] == "pwd4"
    assert kwargs["account_id"] == "acc4"
    assert kwargs["interval_seconds"] == 42
    assert set(kwargs["assets"]) == set(HYPOTHESIS4_ASSETS)


def test_run_hypothesis4_loop_default_startup_offset_is_40s():
    # Échelonnement des 6 process (24/08/2026, voir docs/DECISIONS.md) :
    # executor_loop=0s, trend_executor=10s, H2=20s, H3=30s, H4=40s, H5=50s.
    config = MagicMock()
    with patch("src.hypothesis4_executor.run_technical_strategy_loop") as mock_loop, patch("src.hypothesis4_executor.compute_expensive_hours_by_asset", return_value={}):
        run_hypothesis4_loop(config, "db.sqlite")
    _, kwargs = mock_loop.call_args
    assert kwargs["startup_offset_seconds"] == 40


def test_run_hypothesis4_loop_raises_configerror_when_credentials_missing():
    from src.config import ConfigError

    config = MagicMock()
    config.capital_api_key_hypothesis4 = None
    config.capital_identifier_hypothesis4 = None
    config.capital_api_password_hypothesis4 = None
    config.capital_account_id_hypothesis4 = None

    with patch("src.hypothesis4_executor.compute_expensive_hours_by_asset", return_value={}):
        try:
            run_hypothesis4_loop(config, "db.sqlite")
            assert False, "ConfigError attendue"
        except ConfigError:
            pass


def test_run_hypothesis4_loop_activates_expensive_hours_filter(tmp_path):
    # Point 1 (30/08/2026, voir docs/DECISIONS.md) : H4 est close cote
    # recherche mais reste en demo - le filtre doit rester actif.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    config = MagicMock()
    with patch("src.hypothesis4_executor.run_technical_strategy_loop") as mock_loop, \
         patch("src.hypothesis4_executor.compute_expensive_hours_by_asset", return_value={"GBPUSD": {21}}) as mock_expensive:
        run_hypothesis4_loop(config, db_path)
    mock_expensive.assert_called_once_with(db_path)
    _, kwargs = mock_loop.call_args
    assert kwargs["expensive_hours_by_asset"] == {"GBPUSD": {21}}
