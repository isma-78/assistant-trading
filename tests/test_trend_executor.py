"""
Tests de trend_executor — orchestration du Flux B. La logique de décision
d'entrée elle-même (trend_strategy.evaluate_entry) est testée séparément
à 100% dans test_trend_strategy.py ; ces tests couvrent le câblage DB +
CapitalClient (signal généré -> enregistré -> traité par le même
executor.open_signal que Station X), pas une exigence de couverture
totale — cohérent avec le traitement déjà appliqué à
telegram_listener.run_listener() et executor.run_executor_loop().
"""

from unittest.mock import MagicMock, patch

from src.db import connection_scope, get_connection, init_db
from src.market_data import Candle
from src.trend_executor import (
    CANDLE_COUNT,
    HYPOTHESIS_ASSETS,
    _generate_and_queue_signal,
    _has_active_hypothesis_signal_or_trade,
)
from src.trend_strategy import DONCHIAN_PERIOD, MA_PERIOD


def _flat_candles(closes):
    return [Candle(time_utc=str(i), open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


def _breakout_candles():
    # Régime long clair (dernière clôture très au-dessus d'une longue
    # base plate) + rupture nette du canal de Donchian(20).
    base = [1.10] * (MA_PERIOD - DONCHIAN_PERIOD - 1)
    channel = [1.10] * DONCHIAN_PERIOD
    breakout = [1.20]
    return _flat_candles(base + channel + breakout)


def test_has_active_signal_or_trade_false_when_nothing_pending(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert _has_active_hypothesis_signal_or_trade(db_path, "EURUSD") is False


def test_has_active_signal_or_trade_true_when_signal_pending(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, 'trend_strategy', '2026-08-20T00:00:00Z', 'texte', 'signal')"
        ).lastrowid
        conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
            "confiance, statut, created_at) "
            "VALUES (?, 'hypothesis', 'EURUSD', 'long', 1.1, 1.1, 1.09, 1.0, 'a_valider', '2026-08-20T00:00:00Z')",
            (raw_id,),
        )
    assert _has_active_hypothesis_signal_or_trade(db_path, "EURUSD") is True


def test_has_active_signal_or_trade_true_when_trade_open(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('deal-1', 'hypothesis', 'EURUSD', 'demo', 'long', 100, 1.09, 1.09, 10.0, 2.0, "
            "'2026-08-20T00:00:00Z', 'ouvert')"
        )
    assert _has_active_hypothesis_signal_or_trade(db_path, "EURUSD") is True


def test_has_active_signal_or_trade_ignores_other_source(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('deal-1', 'stationx', 'EURUSD', 'demo', 'long', 100, 1.09, 1.09, 10.0, 2.0, "
            "'2026-08-20T00:00:00Z', 'ouvert')"
        )
    # Un trade stationx actif sur le même actif ne doit jamais bloquer le Flux B
    assert _has_active_hypothesis_signal_or_trade(db_path, "EURUSD") is False


def test_generate_and_queue_signal_no_signal_when_flat(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    client = MagicMock()
    client.get_prices.return_value = {"prices": []}
    _generate_and_queue_signal(db_path, client, "EURUSD")
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0
    finally:
        conn.close()


def test_generate_and_queue_signal_inserts_raw_message_and_signal(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    candles = _breakout_candles()
    prices = [
        {
            "snapshotTimeUTC": c.time_utc,
            "openPrice": {"bid": c.open, "ask": c.open},
            "highPrice": {"bid": c.high, "ask": c.high},
            "lowPrice": {"bid": c.low, "ask": c.low},
            "closePrice": {"bid": c.close, "ask": c.close},
        }
        for c in candles
    ]
    client = MagicMock()
    client.get_prices.return_value = {"prices": prices}

    _generate_and_queue_signal(db_path, client, "EURUSD")

    conn = get_connection(db_path)
    try:
        signal = conn.execute("SELECT * FROM signals").fetchone()
        assert signal is not None
        assert signal["source"] == "hypothesis"
        assert signal["actif"] == "EURUSD"
        assert signal["sens"] == "long"
        assert signal["statut"] == "a_valider"
        raw = conn.execute("SELECT * FROM raw_messages WHERE id = ?", (signal["raw_message_id"],)).fetchone()
        assert raw["channel"] == "trend_strategy"
        assert "Donchian" in raw["raw_text"]
    finally:
        conn.close()


def test_generate_and_queue_signal_skipped_when_already_active(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('deal-1', 'hypothesis', 'EURUSD', 'demo', 'long', 100, 1.09, 1.09, 10.0, 2.0, "
            "'2026-08-20T00:00:00Z', 'ouvert')"
        )
    client = MagicMock()
    _generate_and_queue_signal(db_path, client, "EURUSD")
    client.get_prices.assert_not_called()  # court-circuité avant même d'interroger le marché
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0
    finally:
        conn.close()


def test_hypothesis_assets_matches_hypotheses_md():
    # Garde-fou de non-régression : les 5 actifs de l'Hypothèse #1
    # (docs/HYPOTHESES.md) doivent rester exactement ceux-ci sans une
    # nouvelle entrée datée dans ce fichier.
    assert set(HYPOTHESIS_ASSETS) == {"US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD"}


def test_candle_count_covers_ma_period_with_margin():
    assert CANDLE_COUNT > MA_PERIOD
