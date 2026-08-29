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
from src.hypothesis1_strategy_v2 import ADX_PERIOD, ADX_THRESHOLD, MA_PERIOD, compute_adx_series
from src.market_data import Candle
from src.trend_executor import (
    CANDLE_COUNT,
    HYPOTHESIS_ASSETS,
    _generate_and_queue_signal,
    _has_active_hypothesis_signal_or_trade,
    run_trend_loop,
)


def _c(i, high, low, close):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close)


def _adx_crossing_candles():
    """Série choppy puis tendance nette, tronquée EXACTEMENT au premier
    franchissement d'ADX(14) au-dessus de ADX_THRESHOLD (même
    construction que test_hypothesis1_strategy_v2.py, vérifiée
    numériquement pendant le développement, voir docs/DECISIONS.md)."""
    candles, price, t = [], 100.0, 0
    for i in range(MA_PERIOD + 40):
        price += 0.1 if i % 2 == 0 else -0.1
        candles.append(_c(t, price + 0.3, price - 0.3, price)); t += 1
    for _ in range(40):
        price += 0.5
        candles.append(_c(t, price + 0.3, price - 0.3, price)); t += 1

    adx = compute_adx_series(candles, ADX_PERIOD)
    for i in range(1, len(adx)):
        if adx[i - 1] is not None and adx[i] is not None and adx[i - 1] <= ADX_THRESHOLD < adx[i]:
            return candles[: i + 1]
    raise AssertionError("fixture n'a produit aucun franchissement ADX — construction à revoir")


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
            "VALUES (?, 'hypothesis_v2', 'EURUSD', 'long', 1.1, 1.1, 1.09, 1.0, 'a_valider', '2026-08-20T00:00:00Z')",
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
            "VALUES ('deal-1', 'hypothesis_v2', 'EURUSD', 'demo', 'long', 100, 1.09, 1.09, 10.0, 2.0, "
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

    candles = _adx_crossing_candles()
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
        assert signal["source"] == "hypothesis_v2"  # refonte L1, 29/08/2026 (voir docs/DECISIONS.md)
        assert signal["actif"] == "EURUSD"
        assert signal["sens"] == "long"
        assert signal["statut"] == "a_valider"
        raw = conn.execute("SELECT * FROM raw_messages WHERE id = ?", (signal["raw_message_id"],)).fetchone()
        assert raw["channel"] == "trend_strategy"
        assert "ADX" in raw["raw_text"]
    finally:
        conn.close()


def test_generate_and_queue_signal_skipped_when_already_active(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('deal-1', 'hypothesis_v2', 'EURUSD', 'demo', 'long', 100, 1.09, 1.09, 10.0, 2.0, "
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


def test_hypothesis_assets_matches_asset_whitelist():
    # Garde-fou de non-régression : depuis l'extension du périmètre du
    # 20/08/2026 (docs/DECISIONS.md), le Flux B couvre les actifs de
    # la liste blanche — doit rester exactement ceux-ci sans une
    # nouvelle entrée datée dans trend_executor.py. CHFJPY ajoutée le
    # 28/08/2026 (voir docs/DECISIONS.md, point 2).
    assert set(HYPOTHESIS_ASSETS) == {
        "US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD", "CHFJPY",
    }


def test_candle_count_may_be_insufficient_for_widest_l1_ma_period():
    # CANDLE_COUNT (220) est un générique partagé (trend_strategy.
    # MA_PERIOD(200)+20), indépendant de la grille L1 pré-enregistrée.
    # `hypothesis1_strategy_v2.MA_PERIOD` peut être calibré jusqu'à 250
    # (+ SLOPE_LOOKBACK=5) — CANDLE_COUNT ne le couvre alors PLUS.
    # Documenté ici comme point à revérifier avant tout déploiement de
    # L1 avec un MA_PERIOD calibré au-delà de 200 (voir docs/DECISIONS.md,
    # point C, Hypothèse #1) — pas corrigé ici (hors périmètre : aucun
    # déploiement n'a lieu dans ce chantier, point A).
    assert CANDLE_COUNT == 220
    assert MA_PERIOD in (100, 150, 200, 250)
    widest_requirement = 250 + 5  # MA_PERIOD max + SLOPE_LOOKBACK
    assert CANDLE_COUNT < widest_requirement  # constat, pas une assertion "tout va bien"


def test_run_trend_loop_untouched_by_session_multi_timeframe_layer():
    # Régression stricte demandée explicitement (23/08/2026, couche
    # session/multi-timeframe H2-H5, voir docs/DECISIONS.md) : H1 doit
    # rester identique bit pour bit. Vérifié ici au niveau le plus direct
    # possible — run_trend_loop n'appelle jamais run_technical_strategy_
    # loop avec session_gated ni require_regime_confirmation, donc H1
    # reçoit strictement leurs valeurs par défaut (False), quoi qu'il
    # advienne du reste de la couche.
    config = MagicMock()
    with patch("src.trend_executor.run_technical_strategy_loop") as mock_loop:
        run_trend_loop(config, "db.sqlite", interval_seconds=42)

    mock_loop.assert_called_once()
    _, kwargs = mock_loop.call_args
    assert kwargs["resolution"] == "HOUR"
    assert "session_gated" not in kwargs
    assert "require_regime_confirmation" not in kwargs


def test_run_trend_loop_default_startup_offset_is_10s():
    # Échelonnement des 6 process (24/08/2026, voir docs/DECISIONS.md) :
    # executor_loop=0s, trend_executor=10s, H2=20s, H3=30s, H4=40s, H5=50s.
    config = MagicMock()
    with patch("src.trend_executor.run_technical_strategy_loop") as mock_loop:
        run_trend_loop(config, "db.sqlite")
    _, kwargs = mock_loop.call_args
    assert kwargs["startup_offset_seconds"] == 10
