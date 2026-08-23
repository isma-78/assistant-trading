"""
Tests de technical_strategy_executor — moteur générique extrait le
21/08/2026 (voir docs/DECISIONS.md). La logique de décision d'entrée
elle-même (trend_strategy.evaluate_entry, ict_strategy.evaluate_entry)
est testée séparément à 100% dans son propre fichier ; ceux-ci couvrent
le câblage générique (signal généré -> enregistré, garde-fous de
configuration), pas une exigence de couverture totale sur
run_technical_strategy_loop lui-même — même régime que
test_trend_executor.py (dont l'existence, avant le refactor du
21/08/2026, ne couvrait déjà pas run_trend_loop en totalité).
"""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.config import ConfigError
from src.db import connection_scope, get_connection, init_db
from src.market_data import Candle
from src.technical_strategy_executor import (
    _default_describe_signal,
    _generate_and_queue_signal,
    _has_active_signal_or_trade,
    run_technical_strategy_loop,
)


@dataclass(frozen=True)
class _FakeSignal:
    direction: str
    entry_price: float
    stop_price: float
    confidence: float = 1.0


@dataclass(frozen=True)
class _FakeSignalWithTakeProfit:
    # Forme d'un MeanReversionSignal (Hypothèse #4) : porte take_profit,
    # contrairement à TrendSignal/ICT (_FakeSignal ci-dessus).
    direction: str
    entry_price: float
    stop_price: float
    take_profit: float
    confidence: float = 1.0


@dataclass(frozen=True)
class _FakeSignalWithTp1Tp2:
    # Forme d'un Hypothesis5Signal (Hypothèse #5) : porte tp1/tp2,
    # contrairement à TrendSignal/ICT (_FakeSignal ci-dessus) et à
    # MeanReversionSignal (_FakeSignalWithTakeProfit, take_profit unique).
    direction: str
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float
    confidence: float = 1.0


def _fake_entry_fn(asset, candles):
    return _FakeSignal(direction="long", entry_price=1.2, stop_price=1.1)


def _fake_entry_fn_with_take_profit(asset, candles):
    return _FakeSignalWithTakeProfit(direction="long", entry_price=1.2, stop_price=1.1, take_profit=1.15)


def _fake_entry_fn_with_tp1_tp2(asset, candles):
    return _FakeSignalWithTp1Tp2(direction="long", entry_price=1.2, stop_price=1.1, tp1=1.3, tp2=1.4)


def _no_entry_fn(asset, candles):
    return None


def test_default_describe_signal_mentions_asset_and_prices():
    text = _default_describe_signal("Hypothèse #3", "GOLD", _FakeSignal("long", 2400.0, 2380.0))
    assert "GOLD" in text
    assert "long" in text
    assert "2400" in text
    assert "2380" in text


def test_has_active_signal_or_trade_scoped_by_source(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('deal-1', 'hypothesis3', 'EURUSD', 'demo', 'long', 100, 1.09, 1.09, 10.0, 2.0, "
            "'2026-08-21T00:00:00Z', 'ouvert')"
        )
    assert _has_active_signal_or_trade(db_path, "EURUSD", "hypothesis3") is True
    # Une autre source sur le même actif ne doit jamais bloquer celle-ci
    assert _has_active_signal_or_trade(db_path, "EURUSD", "hypothesis2") is False
    assert _has_active_signal_or_trade(db_path, "EURUSD", "hypothesis") is False


def test_generate_and_queue_signal_no_signal(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_prices.return_value = {"prices": []}
    _generate_and_queue_signal(
        db_path, client, "EURUSD",
        source="hypothesis3", resolution="MINUTE_15", entry_fn=_no_entry_fn,
        channel="test_channel", hypothesis_label="Hypothèse #3",
    )
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0
    finally:
        conn.close()


def test_generate_and_queue_signal_inserts_with_correct_source_and_channel(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_prices.return_value = {
        "prices": [{
            "snapshotTimeUTC": "2026-08-21T00:00:00Z",
            "openPrice": {"bid": 1.2, "ask": 1.2}, "highPrice": {"bid": 1.2, "ask": 1.2},
            "lowPrice": {"bid": 1.2, "ask": 1.2}, "closePrice": {"bid": 1.2, "ask": 1.2},
        }]
    }
    _generate_and_queue_signal(
        db_path, client, "EURUSD",
        source="hypothesis3", resolution="MINUTE_15", entry_fn=_fake_entry_fn,
        channel="hypothesis3_channel", hypothesis_label="Hypothèse #3",
    )
    conn = get_connection(db_path)
    try:
        signal = conn.execute("SELECT * FROM signals").fetchone()
        assert signal is not None
        assert signal["source"] == "hypothesis3"
        assert signal["actif"] == "EURUSD"
        raw = conn.execute("SELECT * FROM raw_messages WHERE id = ?", (signal["raw_message_id"],)).fetchone()
        assert raw["channel"] == "hypothesis3_channel"
        assert "Hypothèse #3" in raw["raw_text"]
    finally:
        conn.close()
    client.get_prices.assert_called_once_with("EURUSD", resolution="MINUTE_15", max_bars=220)
    assert signal["tp1"] is None
    assert signal["take_profit"] is None  # _FakeSignal n'a pas ce champ (TrendSignal/ICT) -> getattr replie sur None


def test_generate_and_queue_signal_persists_take_profit_never_tp1_tp2(tmp_path):
    # Hypothèse #4 (mean_reversion_strategy.MeanReversionSignal) : le
    # champ take_profit doit atterrir dans signals.take_profit, JAMAIS
    # dans tp1/tp2 — voir docs/DECISIONS.md (21/08/2026) pour la raison :
    # le dispatch de gestion de position d'executor.py distingue les
    # trois mécanismes de sortie par la colonne renseignée.
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_prices.return_value = {
        "prices": [{
            "snapshotTimeUTC": "2026-08-21T00:00:00Z",
            "openPrice": {"bid": 1.2, "ask": 1.2}, "highPrice": {"bid": 1.2, "ask": 1.2},
            "lowPrice": {"bid": 1.2, "ask": 1.2}, "closePrice": {"bid": 1.2, "ask": 1.2},
        }]
    }
    _generate_and_queue_signal(
        db_path, client, "EURUSD",
        source="hypothesis4", resolution="HOUR", entry_fn=_fake_entry_fn_with_take_profit,
        channel="hypothesis4_channel", hypothesis_label="Hypothèse #4",
    )
    conn = get_connection(db_path)
    try:
        signal = conn.execute("SELECT * FROM signals").fetchone()
        assert signal["take_profit"] == pytest.approx(1.15)
        assert signal["tp1"] is None
        assert signal["tp2"] is None
    finally:
        conn.close()


def test_generate_and_queue_signal_persists_tp1_tp2_never_take_profit(tmp_path):
    # Hypothèse #5 (hypothesis5_strategy.Hypothesis5Signal, 23/08/2026) :
    # tp1/tp2 doivent atterrir dans signals.tp1/tp2 (dispatch Station X
    # dans executor._evaluate_position_management), JAMAIS dans
    # signals.take_profit (réservé au dispatch H4) — voir
    # docs/DECISIONS.md (21/08/2026 puis 23/08/2026).
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_prices.return_value = {
        "prices": [{
            "snapshotTimeUTC": "2026-08-23T00:00:00Z",
            "openPrice": {"bid": 1.2, "ask": 1.2}, "highPrice": {"bid": 1.2, "ask": 1.2},
            "lowPrice": {"bid": 1.2, "ask": 1.2}, "closePrice": {"bid": 1.2, "ask": 1.2},
        }]
    }
    _generate_and_queue_signal(
        db_path, client, "EURUSD",
        source="hypothesis5", resolution="HOUR", entry_fn=_fake_entry_fn_with_tp1_tp2,
        channel="hypothesis5_channel", hypothesis_label="Hypothèse #5",
    )
    conn = get_connection(db_path)
    try:
        signal = conn.execute("SELECT * FROM signals").fetchone()
        assert signal["tp1"] == pytest.approx(1.3)
        assert signal["tp2"] == pytest.approx(1.4)
        assert signal["take_profit"] is None
    finally:
        conn.close()


def test_generate_and_queue_signal_uses_custom_describe_signal(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_prices.return_value = {
        "prices": [{
            "snapshotTimeUTC": "2026-08-21T00:00:00Z",
            "openPrice": {"bid": 1.2, "ask": 1.2}, "highPrice": {"bid": 1.2, "ask": 1.2},
            "lowPrice": {"bid": 1.2, "ask": 1.2}, "closePrice": {"bid": 1.2, "ask": 1.2},
        }]
    }
    _generate_and_queue_signal(
        db_path, client, "EURUSD",
        source="hypothesis2", resolution="HOUR", entry_fn=_fake_entry_fn,
        channel="hypothesis2_channel", hypothesis_label="Hypothèse #2",
        describe_signal=lambda label, asset, signal: f"CUSTOM {label} {asset}",
    )
    conn = get_connection(db_path)
    try:
        signal = conn.execute("SELECT * FROM signals").fetchone()
        raw = conn.execute("SELECT * FROM raw_messages WHERE id = ?", (signal["raw_message_id"],)).fetchone()
        assert raw["raw_text"] == "CUSTOM Hypothèse #2 EURUSD"
    finally:
        conn.close()


def test_run_technical_strategy_loop_raises_configerror_missing_credentials(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    config = MagicMock()
    with pytest.raises(ConfigError, match="identifiants"):
        run_technical_strategy_loop(
            config, db_path,
            source="hypothesis2", assets=["EURUSD"], resolution="HOUR", entry_fn=_no_entry_fn,
            api_key=None, identifier=None, password=None, account_id="some-account",
            channel="c", process_name="p", hypothesis_label="Hypothèse #2",
        )


def test_run_technical_strategy_loop_raises_configerror_missing_account_id(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    config = MagicMock()
    with pytest.raises(ConfigError, match="compte"):
        run_technical_strategy_loop(
            config, db_path,
            source="hypothesis2", assets=["EURUSD"], resolution="HOUR", entry_fn=_no_entry_fn,
            api_key="key", identifier="id", password="pwd", account_id=None,
            channel="c", process_name="p", hypothesis_label="Hypothèse #2",
        )
