from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.db import connection_scope, init_db
from src.hypothesis2_executor import (
    HYPOTHESIS2_ASSETS,
    _describe_signal,
    run_hypothesis2_loop,
)
import src.hypothesis2_strategy_v2 as _h2_mod


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
    assert kwargs["resolution"] == "HOUR"  # amendement 29/08/2026 (voir docs/DECISIONS.md) : profondeur M15 insuffisante
    assert kwargs["extra_resolutions"] == ["HOUR_4", "DAY"]  # confluence multi-timeframe L2, remappe depuis HOUR natif
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


def test_run_hypothesis2_loop_applies_and_logs_confirmed_combo(tmp_path, caplog):
    # Point 17 (29/08/2026, voir docs/DECISIONS.md) : le combo confirme
    # doit etre lu depuis rule_changes (cle H2_v2, statut='applique') et
    # le log de demarrage doit montrer les valeurs REELLEMENT actives
    # sur le module, pas seulement leur presence en base.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        for variable, valeur in [
            ("H2_v2.EMA_PERIOD", "20"), ("H2_v2.RSI_THRESHOLD", "55"),
            ("H2_v2.N_TF", "3"), ("H2_v2.SCORE_THRESHOLD", "1.0"),
        ]:
            conn.execute(
                "INSERT INTO rule_changes (proposed_at, variable, constat_stat, ajustement_propose, "
                "statut, validated_at, applied_at) "
                "VALUES ('2026-08-29T00:00:00Z', ?, 'confirmation point 17', ?, 'applique', "
                "'2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z')",
                (variable, valeur),
            )

    saved = {name: getattr(_h2_mod, name) for name in ("EMA_PERIOD", "RSI_THRESHOLD", "N_TF", "SCORE_THRESHOLD")}
    try:
        config = MagicMock()
        with patch("src.hypothesis2_executor.run_technical_strategy_loop"), caplog.at_level("INFO"):
            run_hypothesis2_loop(config, db_path)

        assert _h2_mod.EMA_PERIOD == 20
        assert _h2_mod.RSI_THRESHOLD == 55.0
        assert _h2_mod.N_TF == 3
        assert _h2_mod.SCORE_THRESHOLD == 1.0

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "EMA_PERIOD=20" in log_text
        assert "RSI_THRESHOLD=55.0" in log_text
        assert "N_TF=3" in log_text
        assert "SCORE_THRESHOLD=1.0" in log_text
    finally:
        for name, value in saved.items():
            setattr(_h2_mod, name, value)


def test_run_hypothesis2_loop_logs_hardcoded_values_when_no_override(tmp_path, caplog):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    saved = {name: getattr(_h2_mod, name) for name in ("EMA_PERIOD", "RSI_THRESHOLD", "N_TF", "SCORE_THRESHOLD")}
    try:
        config = MagicMock()
        with patch("src.hypothesis2_executor.run_technical_strategy_loop"), caplog.at_level("INFO"):
            run_hypothesis2_loop(config, db_path)
        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "AUCUN" in log_text
    finally:
        for name, value in saved.items():
            setattr(_h2_mod, name, value)


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


def test_run_hypothesis2_loop_never_activates_expensive_hours_filter():
    # Point 2 (30/08/2026, voir docs/DECISIONS.md) : H2 se deploie
    # EXACTEMENT comme elle a ete confirmee (point 17), sans le filtre
    # d'heures cheres - activer ce filtre reviendrait a deployer une
    # configuration differente de celle validee. Jamais meme importe
    # dans ce module.
    import src.hypothesis2_executor as h2_executor_mod
    assert not hasattr(h2_executor_mod, "compute_expensive_hours_by_asset")

    config = MagicMock()
    with patch("src.hypothesis2_executor.run_technical_strategy_loop") as mock_loop:
        run_hypothesis2_loop(config, "db.sqlite")
    _, kwargs = mock_loop.call_args
    assert "expensive_hours_by_asset" not in kwargs
