from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.causal_analyzer import (
    CATEGORY_ANOMALIE_TECHNIQUE,
    CATEGORY_EVENEMENT_MARCHE,
    CATEGORY_HYPOTHESE_PATTERN,
    CORRELATED_MARKET_EVENT_MIN_OTHER_ASSETS,
    SLIPPAGE_ANOMALY_SPREAD_MULTIPLIER,
    CausalContext,
    TradeWindowEntry,
    _has_slippage_anomaly,
    build_analyse_texte,
    classify_category,
    compute_causal_analysis,
    compute_correlated_exposure,
    gather_causal_context,
    record_causal_analysis,
    window_start,
)
from src.db import connection_scope, init_db
from src.envelope_store import load_or_create_envelope

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 15, 0, 0, tzinfo=UTC)  # lundi


def _entry(trade_id, r=None, ouvert_at="2026-08-24T10:00:00+00:00", ferme_at="2026-08-24T11:00:00+00:00",
           slippage=None, spread=None, actif="GOLD", source="stationx", direction="long"):
    return TradeWindowEntry(
        trade_id=trade_id, actif=actif, source=source, direction=direction, r_multiple_total=r,
        ouvert_at=ouvert_at, ferme_at=ferme_at, slippage_entree=slippage, spread_at_signal=spread,
    )


# ---------------------------------------------------------------------------
# window_start
# ---------------------------------------------------------------------------

def test_window_start_day_r_midnight_utc():
    start = window_start("day_r", NOW)
    assert start == datetime(2026, 8, 24, 0, 0, 0, tzinfo=UTC)


def test_window_start_week_r_monday_utc():
    start = window_start("week_r", NOW)
    assert start == datetime(2026, 8, 24, 0, 0, 0, tzinfo=UTC)  # NOW est déjà un lundi


def test_window_start_week_r_midweek():
    wednesday = datetime(2026, 8, 26, 15, 0, 0, tzinfo=UTC)
    start = window_start("week_r", wednesday)
    assert start == datetime(2026, 8, 24, 0, 0, 0, tzinfo=UTC)


def test_window_start_drawdown_r_none():
    assert window_start("drawdown_r", NOW) is None


def test_window_start_unknown_breaker_raises():
    with pytest.raises(ValueError):
        window_start("manual_pause", NOW)


def test_window_start_naive_now_raises():
    with pytest.raises(ValueError):
        window_start("day_r", datetime(2026, 8, 24, 15, 0, 0))


# ---------------------------------------------------------------------------
# compute_correlated_exposure
# ---------------------------------------------------------------------------

def test_correlated_exposure_overlapping_losing_trades():
    trades = [
        _entry(1, r=-0.5, ouvert_at="2026-08-24T10:00:00+00:00", ferme_at="2026-08-24T12:00:00+00:00"),
        _entry(2, r=-0.3, ouvert_at="2026-08-24T11:00:00+00:00", ferme_at="2026-08-24T13:00:00+00:00"),
    ]
    assert compute_correlated_exposure(trades) == [(1, 2)]


def test_correlated_exposure_non_overlapping_excluded():
    trades = [
        _entry(1, r=-0.5, ouvert_at="2026-08-24T10:00:00+00:00", ferme_at="2026-08-24T11:00:00+00:00"),
        _entry(2, r=-0.3, ouvert_at="2026-08-24T12:00:00+00:00", ferme_at="2026-08-24T13:00:00+00:00"),
    ]
    assert compute_correlated_exposure(trades) == []


def test_correlated_exposure_winning_trades_excluded():
    trades = [
        _entry(1, r=0.5, ouvert_at="2026-08-24T10:00:00+00:00", ferme_at="2026-08-24T12:00:00+00:00"),
        _entry(2, r=-0.3, ouvert_at="2026-08-24T11:00:00+00:00", ferme_at="2026-08-24T13:00:00+00:00"),
    ]
    assert compute_correlated_exposure(trades) == []


def test_correlated_exposure_open_trade_excluded():
    trades = [
        _entry(1, r=-0.5, ouvert_at="2026-08-24T10:00:00+00:00", ferme_at=None),
        _entry(2, r=-0.3, ouvert_at="2026-08-24T11:00:00+00:00", ferme_at="2026-08-24T13:00:00+00:00"),
    ]
    assert compute_correlated_exposure(trades) == []


def test_correlated_exposure_no_r_multiple_excluded():
    trades = [
        _entry(1, r=None, ouvert_at="2026-08-24T10:00:00+00:00", ferme_at="2026-08-24T12:00:00+00:00"),
        _entry(2, r=-0.3, ouvert_at="2026-08-24T11:00:00+00:00", ferme_at="2026-08-24T13:00:00+00:00"),
    ]
    assert compute_correlated_exposure(trades) == []


def test_correlated_exposure_empty_list():
    assert compute_correlated_exposure([]) == []


# ---------------------------------------------------------------------------
# _has_slippage_anomaly
# ---------------------------------------------------------------------------

def test_has_slippage_anomaly_true_beyond_threshold():
    trades = [_entry(1, slippage=1.0, spread=0.1)]  # 1.0 > 5.0*0.1
    assert _has_slippage_anomaly(trades) is True


def test_has_slippage_anomaly_false_within_threshold():
    trades = [_entry(1, slippage=0.1, spread=0.1)]  # 0.1 <= 5.0*0.1
    assert _has_slippage_anomaly(trades) is False


def test_has_slippage_anomaly_none_values_skipped():
    trades = [_entry(1, slippage=None, spread=0.1), _entry(2, slippage=1.0, spread=None)]
    assert _has_slippage_anomaly(trades) is False


def test_has_slippage_anomaly_zero_spread_skipped():
    trades = [_entry(1, slippage=1.0, spread=0.0)]
    assert _has_slippage_anomaly(trades) is False


def test_has_slippage_anomaly_empty_list():
    assert _has_slippage_anomaly([]) is False


# ---------------------------------------------------------------------------
# classify_category
# ---------------------------------------------------------------------------

def test_classify_api_error_anomaly_takes_priority():
    result = classify_category(
        has_api_error_anomaly=True, has_slippage_anomaly=False,
        other_assets_triggered_same_day=10, macro_events_in_window=[{"impact": "fort"}],
    )
    assert result == CATEGORY_ANOMALIE_TECHNIQUE


def test_classify_slippage_anomaly():
    result = classify_category(
        has_api_error_anomaly=False, has_slippage_anomaly=True,
        other_assets_triggered_same_day=0, macro_events_in_window=[],
    )
    assert result == CATEGORY_ANOMALIE_TECHNIQUE


def test_classify_market_event_via_breadth():
    result = classify_category(
        has_api_error_anomaly=False, has_slippage_anomaly=False,
        other_assets_triggered_same_day=CORRELATED_MARKET_EVENT_MIN_OTHER_ASSETS, macro_events_in_window=[],
    )
    assert result == CATEGORY_EVENEMENT_MARCHE


def test_classify_market_event_below_breadth_threshold_not_market_event():
    result = classify_category(
        has_api_error_anomaly=False, has_slippage_anomaly=False,
        other_assets_triggered_same_day=CORRELATED_MARKET_EVENT_MIN_OTHER_ASSETS - 1, macro_events_in_window=[],
    )
    assert result == CATEGORY_HYPOTHESE_PATTERN


def test_classify_market_event_via_macro_fort():
    result = classify_category(
        has_api_error_anomaly=False, has_slippage_anomaly=False,
        other_assets_triggered_same_day=0, macro_events_in_window=[{"impact": "fort"}],
    )
    assert result == CATEGORY_EVENEMENT_MARCHE


def test_classify_macro_faible_does_not_trigger_market_event():
    result = classify_category(
        has_api_error_anomaly=False, has_slippage_anomaly=False,
        other_assets_triggered_same_day=0, macro_events_in_window=[{"impact": "faible"}],
    )
    assert result == CATEGORY_HYPOTHESE_PATTERN


def test_classify_residual_hypothese_pattern():
    result = classify_category(
        has_api_error_anomaly=False, has_slippage_anomaly=False,
        other_assets_triggered_same_day=0, macro_events_in_window=[],
    )
    assert result == CATEGORY_HYPOTHESE_PATTERN


# ---------------------------------------------------------------------------
# build_analyse_texte
# ---------------------------------------------------------------------------

def test_analyse_texte_anomalie_technique_api_error():
    ctx = CausalContext(trades=[_entry(1, r=-1.0)])
    text = build_analyse_texte(
        CATEGORY_ANOMALIE_TECHNIQUE, "GOLD", "stationx", "day_r", ctx,
        has_api_error_anomaly=True, has_slippage_anomaly=False,
    )
    assert "anomalie_technique" in text
    assert "erreurs API" in text


def test_analyse_texte_anomalie_technique_slippage():
    ctx = CausalContext(trades=[_entry(1, r=-1.0)])
    text = build_analyse_texte(
        CATEGORY_ANOMALIE_TECHNIQUE, "GOLD", "stationx", "day_r", ctx,
        has_api_error_anomaly=False, has_slippage_anomaly=True,
    )
    assert "slippage" in text
    assert f"{SLIPPAGE_ANOMALY_SPREAD_MULTIPLIER:.0f}x" in text


def test_analyse_texte_evenement_marche_breadth():
    ctx = CausalContext(trades=[], other_assets_triggered_same_day=3)
    text = build_analyse_texte(
        CATEGORY_EVENEMENT_MARCHE, "GOLD", "stationx", "week_r", ctx,
        has_api_error_anomaly=False, has_slippage_anomaly=False,
    )
    assert "evenement_marche" in text
    assert "3 autre" in text


def test_analyse_texte_evenement_marche_macro():
    ctx = CausalContext(trades=[], macro_events_in_window=[{"impact": "fort"}])
    text = build_analyse_texte(
        CATEGORY_EVENEMENT_MARCHE, "GOLD", "stationx", "week_r", ctx,
        has_api_error_anomaly=False, has_slippage_anomaly=False,
    )
    assert "macro" in text.lower()


def test_analyse_texte_hypothese_pattern():
    ctx = CausalContext(
        trades=[_entry(1, r=-0.5, ferme_at="2026-08-24T12:00:00+00:00"),
                _entry(2, r=-0.3, ouvert_at="2026-08-24T10:30:00+00:00", ferme_at="2026-08-24T13:00:00+00:00")],
        confidence_nb_trades=15, confidence_esperance_r=0.1,
        correlated_exposure_pairs=[(1, 2)],
    )
    text = build_analyse_texte(
        CATEGORY_HYPOTHESE_PATTERN, "GOLD", "stationx", "drawdown_r", ctx,
        has_api_error_anomaly=False, has_slippage_anomaly=False,
    )
    assert "hypothese_pattern" in text
    assert "1 paire" in text


def test_analyse_texte_hypothese_pattern_no_confidence_data():
    ctx = CausalContext(trades=[], confidence_nb_trades=0, confidence_esperance_r=None)
    text = build_analyse_texte(
        CATEGORY_HYPOTHESE_PATTERN, "GOLD", "stationx", "drawdown_r", ctx,
        has_api_error_anomaly=False, has_slippage_anomaly=False,
    )
    assert "indisponible" in text


# ---------------------------------------------------------------------------
# compute_causal_analysis (assemblage pur)
# ---------------------------------------------------------------------------

def test_compute_causal_analysis_end_to_end_pattern():
    ctx = CausalContext(trades=[_entry(1, r=-0.5)], confidence_nb_trades=5, confidence_esperance_r=-0.1)
    analysis = compute_causal_analysis("GOLD", "stationx", "day_r", ctx)
    assert analysis.categorie == CATEGORY_HYPOTHESE_PATTERN
    assert analysis.trades_concernes_ids == [1]
    assert '"confidence_nb_trades": 5' in analysis.contexte_json
    assert "hypothese_pattern" in analysis.analyse_texte


def test_compute_causal_analysis_end_to_end_technical_anomaly():
    ctx = CausalContext(trades=[_entry(1, r=-1.0)], has_api_error_anomaly=True)
    analysis = compute_causal_analysis("GOLD", "stationx", "day_r", ctx)
    assert analysis.categorie == CATEGORY_ANOMALIE_TECHNIQUE
    assert '"has_api_error_anomaly": true' in analysis.contexte_json


# ---------------------------------------------------------------------------
# Orchestration I/O — DB réelle temporaire
# ---------------------------------------------------------------------------

def _insert_trade(db_path, trade_id_marker, actif, source, ouvert_at, ferme_at, r_multiple,
                   slippage_entree=None, signal_id=None):
    with connection_scope(db_path) as conn:
        return conn.execute(
            "INSERT INTO trades (signal_id, actif, source, mode, direction, taille_initiale, "
            "slippage_entree, stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, ferme_at, r_multiple_total, statut) "
            "VALUES (?, ?, ?, 'demo', 'long', 0.01, ?, 1.0, 1.0, 10.0, 2.0, ?, ?, ?, 'ferme')",
            (signal_id, actif, source, slippage_entree, ouvert_at, ferme_at, r_multiple),
        ).lastrowid


def _insert_signal_with_snapshot(db_path, spread):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (?, 'chan', '2026-08-24T09:00:00+00:00', 'x', 'signal')",
            (1,),
        ).lastrowid
        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, created_at) "
            "VALUES (?, 'stationx', 'GOLD', 'long', '2026-08-24T09:00:00+00:00')",
            (raw_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) VALUES (?, 1.0, 1.0, ?, '2026-08-24T09:00:00+00:00')",
            (signal_id, spread),
        )
    return signal_id


def test_gather_causal_context_reads_trades_and_confidence(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    _insert_trade(db_path, 1, "GOLD", "stationx", "2026-08-24T10:00:00+00:00", "2026-08-24T11:00:00+00:00", -0.5)
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert len(ctx.trades) == 1
    assert ctx.trades[0].r_multiple_total == pytest.approx(-0.5)
    assert ctx.confidence_nb_trades == 1


def test_gather_causal_context_drawdown_r_no_lower_bound(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    _insert_trade(db_path, 1, "GOLD", "stationx", "2020-01-01T10:00:00+00:00", "2020-01-01T11:00:00+00:00", -0.5)
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "drawdown_r", NOW)
    assert len(ctx.trades) == 1  # trade tres ancien quand meme inclus (pas de borne pour drawdown_r)


def test_gather_causal_context_day_r_excludes_older_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    _insert_trade(db_path, 1, "GOLD", "stationx", "2026-08-20T10:00:00+00:00", "2026-08-20T11:00:00+00:00", -0.5)
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert ctx.trades == []


def test_gather_causal_context_other_assets_triggered_same_day(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        for actif in ("EURUSD", "GBPUSD"):
            conn.execute(
                "INSERT INTO circuit_breaker_events (scope, actif, source, breaker_type, triggered_at, r_value) "
                "VALUES ('asset', ?, 'stationx', 'day_r', ?, -2.0)",
                (actif, NOW.isoformat()),
            )
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert ctx.other_assets_triggered_same_day == 2


def test_gather_causal_context_macro_events_in_window(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO macro_events (datetime, devise, intitule, impact) VALUES (?, 'USD', 'NFP', 'fort')",
            (NOW.isoformat(),),
        )
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert len(ctx.macro_events_in_window) == 1
    assert ctx.macro_events_in_window[0]["impact"] == "fort"


def test_gather_causal_context_api_error_anomaly(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO circuit_breaker_events (scope, breaker_type, triggered_at) VALUES ('global', 'api_errors', ?)",
            (NOW.isoformat(),),
        )
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert ctx.has_api_error_anomaly is True


def test_gather_causal_context_no_api_error_anomaly(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert ctx.has_api_error_anomaly is False


def test_gather_causal_context_uses_market_snapshot_spread(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    signal_id = _insert_signal_with_snapshot(db_path, spread=0.3)
    _insert_trade(db_path, 1, "GOLD", "stationx", "2026-08-24T10:00:00+00:00", "2026-08-24T11:00:00+00:00",
                  -0.5, slippage_entree=2.0, signal_id=signal_id)
    ctx = gather_causal_context(db_path, "GOLD", "stationx", "day_r", NOW)
    assert ctx.trades[0].spread_at_signal == pytest.approx(0.3)
    assert ctx.trades[0].slippage_entree == pytest.approx(2.0)


def test_record_causal_analysis_persists_and_returns_id(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    _insert_trade(db_path, 1, "GOLD", "stationx", "2026-08-24T10:00:00+00:00", "2026-08-24T11:00:00+00:00", -0.5)

    log_id = record_causal_analysis(db_path, "GOLD", "stationx", "day_r", NOW)
    assert log_id > 0
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT * FROM causal_analysis_log WHERE id = ?", (log_id,)).fetchone()
    assert row["categorie"] == CATEGORY_HYPOTHESE_PATTERN
    assert row["action_prise"] is None
    assert row["declencheur"] == "day_r:GOLD:stationx"


def test_record_causal_analysis_notifies_on_technical_anomaly(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO circuit_breaker_events (scope, breaker_type, triggered_at) VALUES ('global', 'api_errors', ?)",
            (NOW.isoformat(),),
        )

    with patch("src.causal_analyzer.send_notification") as mock_notify:
        log_id = record_causal_analysis(db_path, "GOLD", "stationx", "day_r", NOW, bot_token="tok", chat_id="42")

    mock_notify.assert_called_once()
    args, _ = mock_notify.call_args
    assert "anomalie technique" in args[2].lower()
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT categorie FROM causal_analysis_log WHERE id = ?", (log_id,)).fetchone()
    assert row["categorie"] == CATEGORY_ANOMALIE_TECHNIQUE


def test_record_causal_analysis_no_notification_without_tokens(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO circuit_breaker_events (scope, breaker_type, triggered_at) VALUES ('global', 'api_errors', ?)",
            (NOW.isoformat(),),
        )
    with patch("src.causal_analyzer.send_notification") as mock_notify:
        record_causal_analysis(db_path, "GOLD", "stationx", "day_r", NOW)
    mock_notify.assert_not_called()


def test_record_causal_analysis_no_notification_when_not_technical(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with patch("src.causal_analyzer.send_notification") as mock_notify:
        record_causal_analysis(db_path, "GOLD", "stationx", "day_r", NOW, bot_token="tok", chat_id="42")
    mock_notify.assert_not_called()


def test_record_causal_analysis_fail_safe_returns_negative_one(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("src.causal_analyzer.gather_causal_context", side_effect=RuntimeError("panne")):
        result = record_causal_analysis(db_path, "GOLD", "stationx", "day_r", NOW)
    assert result == -1
