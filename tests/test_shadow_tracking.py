"""
tests/test_shadow_tracking.py — Suivi forward en shadow des 4 candidates
V2 (06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §6-7). Vérifie
en particulier qu'AUCUN appel broker d'écriture (ordre, clôture,
modification de stop) n'est jamais effectué par `run_shadow_cycle`.
"""

from unittest.mock import MagicMock

import pytest

from src.capital_client import CapitalApiError
from src.db import connection_scope, init_db
from src.market_data import Candle, PriceSnapshot
from src.risk_engine import AssetSpec, RiskCaps, RiskEngine
from src.shadow_tracking import (
    advance_shadow_trade,
    ensure_shadow_epoch,
    has_open_shadow_trade,
    historical_bar_from_candle,
    open_shadow_trade,
    run_shadow_cycle,
)
from src.trend_strategy import TrendSignal


def _candle(i, high, low, close, volume=None):
    return Candle(time_utc=f"2026-01-01T{i:02d}:00:00", open=close, high=high, low=low, close=close, volume=volume)


def _rows(db_path, sql, params=()):
    with connection_scope(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def _engine():
    return RiskEngine(
        caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=500.0),
        whitelist={"GOLD": AssetSpec(symbol="GOLD", min_units=0.01, pip_value_per_unit=0.86)},
    )


# ---------------------------------------------------------------------------
# historical_bar_from_candle
# ---------------------------------------------------------------------------

def test_historical_bar_from_candle_splits_spread_symmetrically():
    candle = _candle(0, 101.0, 99.0, 100.0)
    bar = historical_bar_from_candle(candle, spread=0.2)
    assert bar.close_bid == pytest.approx(99.9)
    assert bar.close_ask == pytest.approx(100.1)
    assert bar.spread_open == pytest.approx(0.2)
    assert bar.time_utc == candle.time_utc


# ---------------------------------------------------------------------------
# open_shadow_trade / has_open_shadow_trade
# ---------------------------------------------------------------------------

def test_open_shadow_trade_creates_row_and_blocks_second_open(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    signal = TrendSignal(asset="GOLD", direction="long", entry_price=100.0, stop_price=98.0, tp1=102.0, tp2=104.0)

    trade_id = open_shadow_trade(db_path, "hypothesis_v3cand", "GOLD", signal, bid=99.9, ask=100.1, now="2026-01-01T00:00:00")
    assert trade_id is not None
    with connection_scope(db_path) as conn:
        assert has_open_shadow_trade(conn, "hypothesis_v3cand", "GOLD")
    row = _rows(db_path, "SELECT * FROM shadow_trades WHERE id = ?", (trade_id,))[0]
    assert row["statut"] == "ouvert" and row["entry_price"] == 100.0 and row["stop_loss_courant"] == 98.0

    second = open_shadow_trade(db_path, "hypothesis_v3cand", "GOLD", signal, bid=99.9, ask=100.1)
    assert second is None  # une seule position virtuelle à la fois
    assert len(_rows(db_path, "SELECT * FROM shadow_trades")) == 1


def test_open_shadow_trade_different_asset_or_source_is_independent(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    signal = TrendSignal(asset="GOLD", direction="long", entry_price=100.0, stop_price=98.0, tp1=102.0, tp2=104.0)
    open_shadow_trade(db_path, "hypothesis_v3cand", "GOLD", signal, bid=99.9, ask=100.1)
    assert open_shadow_trade(db_path, "hypothesis_v3cand", "EURUSD", signal, bid=99.9, ask=100.1) is not None
    assert open_shadow_trade(db_path, "hypothesis2_v3cand", "GOLD", signal, bid=99.9, ask=100.1) is not None
    assert len(_rows(db_path, "SELECT * FROM shadow_trades")) == 3


# ---------------------------------------------------------------------------
# advance_shadow_trade — cycle de vie complet (TP1 -> TP2 -> stop)
# ---------------------------------------------------------------------------

def _open(db_path, direction="long", entry=100.0, stop=90.0, tp1=105.0, tp2=110.0):
    signal = TrendSignal(asset="GOLD", direction=direction, entry_price=entry, stop_price=stop, tp1=tp1, tp2=tp2)
    trade_id = open_shadow_trade(db_path, "hypothesis_v3cand", "GOLD", signal, bid=entry - 0.1, ask=entry + 0.1,
                                 now="2026-01-01T00:00:00")
    return trade_id


def test_advance_shadow_trade_stop_hit_closes_and_records_negative_r(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path)
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    window = [_candle(i, 101, 99, 100) for i in range(20)]
    crash_bar = historical_bar_from_candle(_candle(20, 100, 85, 88), spread=0.2)

    r = advance_shadow_trade(db_path, row, crash_bar, window, _engine(), is_donchian_trailing=False)

    assert r is not None and r < 0
    updated = _rows(db_path, "SELECT * FROM shadow_trades WHERE id = ?", (row["id"],))[0]
    assert updated["statut"] == "ferme" and updated["r_multiple_total"] == pytest.approx(r)
    assert updated["ferme_at"] == crash_bar.time_utc
    partials = _rows(db_path, "SELECT * FROM shadow_partials WHERE shadow_trade_id = ?", (row["id"],))
    assert len(partials) == 1 and partials[0]["palier"] == "sl_ou_tp_final"


def test_advance_shadow_trade_tp1_then_tp2_then_stop_full_lifecycle(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path, entry=100.0, stop=90.0, tp1=105.0, tp2=110.0)
    engine = _engine()
    window = [_candle(i, 101, 99, 100) for i in range(20)]

    # TP1 touché, pas de clôture.
    bar1 = historical_bar_from_candle(_candle(20, 106, 100, 105.5), spread=0.2)
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    r1 = advance_shadow_trade(db_path, row, bar1, window, engine, is_donchian_trailing=False)
    assert r1 is None
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    assert row["statut"] == "ouvert"
    assert row["remaining_fraction"] < 1.0
    partials = _rows(db_path, "SELECT * FROM shadow_partials WHERE shadow_trade_id = ?", (row["id"],))
    assert [p["palier"] for p in partials] == ["tp1"]

    # TP2 touché ensuite.
    bar2 = historical_bar_from_candle(_candle(21, 111, 105, 110.5), spread=0.2)
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    r2 = advance_shadow_trade(db_path, row, bar2, window, engine, is_donchian_trailing=False)
    assert r2 is None
    partials = _rows(db_path, "SELECT * FROM shadow_partials WHERE shadow_trade_id = ?", (row["id"],))
    assert sorted(p["palier"] for p in partials) == ["tp1", "tp2"]

    # Repli final -> stop (désormais au breakeven après TP1/TP2) : clôture complète.
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    stop_now = row["stop_loss_courant"]
    bar3 = historical_bar_from_candle(_candle(22, stop_now + 1, stop_now - 1, stop_now - 0.5), spread=0.2)
    r3 = advance_shadow_trade(db_path, row, bar3, window, engine, is_donchian_trailing=False)
    assert r3 is not None
    final = _rows(db_path, "SELECT * FROM shadow_trades WHERE id = ?", (row["id"],))[0]
    assert final["statut"] == "ferme" and final["remaining_fraction"] == 0.0
    # TP1(+1R)/TP2(+2R) gagnants, reliquat clos près du breakeven : R total nettement positif.
    assert r3 > 0.5


def test_advance_shadow_trade_respects_stop_update_filter(tmp_path):
    """Un refus de resserrement (A8) laisse le stop inchangé — même
    comportement que backtest_engine, réutilisé ici sans modification."""
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path, entry=100.0, stop=90.0, tp1=105.0, tp2=110.0)
    engine = _engine()
    window = [_candle(i, 101, 99, 100) for i in range(20)]
    bar1 = historical_bar_from_candle(_candle(20, 106, 100, 105.5), spread=0.2)
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]

    advance_shadow_trade(db_path, row, bar1, window, engine, is_donchian_trailing=False,
                         stop_update_filter=lambda asset, old, new: False)

    updated = _rows(db_path, "SELECT * FROM shadow_trades WHERE id = ?", (row["id"],))[0]
    assert updated["stop_loss_courant"] == 90.0  # refusé : stop d'origine conservé


def test_advance_shadow_trade_falls_back_to_none_if_manage_open_position_returns_nothing(tmp_path, monkeypatch):
    """Garde-fou fail-safe : si `_manage_open_position` ne renvoyait ni
    trade fermé ni état (ne se produit jamais en pratique, l'état
    NONE renvoie toujours le même `open_state`), `advance_shadow_trade`
    ne lève jamais, renvoie `None`."""
    import src.shadow_tracking as mod
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path)
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    window = [_candle(i, 101, 99, 100) for i in range(20)]
    bar = historical_bar_from_candle(_candle(20, 101, 99, 100), spread=0.2)
    monkeypatch.setattr(mod, "_manage_open_position", lambda *a, **k: (None, None))
    assert advance_shadow_trade(db_path, row, bar, window, _engine(), is_donchian_trailing=False) is None


def test_advance_shadow_trade_none_when_nothing_happens(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path)
    row = _rows(db_path, "SELECT * FROM shadow_trades")[0]
    window = [_candle(i, 101, 99, 100) for i in range(20)]
    quiet_bar = historical_bar_from_candle(_candle(20, 101, 99, 100), spread=0.2)
    assert advance_shadow_trade(db_path, row, quiet_bar, window, _engine(), is_donchian_trailing=False) is None
    assert _rows(db_path, "SELECT * FROM shadow_trades")[0]["statut"] == "ouvert"


# ---------------------------------------------------------------------------
# ensure_shadow_epoch
# ---------------------------------------------------------------------------

def test_ensure_shadow_epoch_writes_once_never_overwrites(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    ensure_shadow_epoch(db_path, "hypothesis_v3cand", now="2026-10-10T00:00:00")
    ensure_shadow_epoch(db_path, "hypothesis_v3cand", now="2026-10-11T00:00:00")
    rows = _rows(db_path, "SELECT * FROM shadow_epochs WHERE source = 'hypothesis_v3cand'")
    assert len(rows) == 1 and rows[0]["started_at"] == "2026-10-10T00:00:00"


# ---------------------------------------------------------------------------
# run_shadow_cycle — AUCUN appel broker d'écriture, jamais
# ---------------------------------------------------------------------------

def _fake_signal_entry_fn(asset, candles):
    return TrendSignal(asset=asset, direction="long", entry_price=100.0, stop_price=90.0, tp1=105.0, tp2=110.0)


def _no_signal_entry_fn(asset, candles):
    return None


def test_run_shadow_cycle_never_calls_order_placing_methods(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    import src.shadow_tracking as mod
    candles = [_candle(i, 101, 99, 100) for i in range(220)]
    monkeypatch.setattr(mod, "get_candles", lambda client, epic, resolution, count: candles)
    monkeypatch.setattr(mod, "get_price_snapshot",
                        lambda client, epic: PriceSnapshot(epic, 99.9, 100.1, 100.0, "TRADEABLE", None))
    client = MagicMock()  # aucune méthode d'écriture ne doit jamais être appelée

    closed = run_shadow_cycle(db_path, client, _engine(),
                              candidates={"hypothesis_v3cand": {"entry_fn": _fake_signal_entry_fn, "assets": ["GOLD"],
                                                                 "extras": [], "donchian": False}})

    assert closed == 0
    for forbidden in ("place_limit_order", "open_position", "close_position", "update_position_stop",
                      "cancel_working_order"):
        assert getattr(client, forbidden).called is False
    assert len(_rows(db_path, "SELECT * FROM shadow_trades")) == 1
    assert len(_rows(db_path, "SELECT * FROM shadow_epochs")) == 1


def test_run_shadow_cycle_no_signal_opens_nothing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    import src.shadow_tracking as mod
    candles = [_candle(i, 101, 99, 100) for i in range(220)]
    monkeypatch.setattr(mod, "get_candles", lambda client, epic, resolution, count: candles)
    monkeypatch.setattr(mod, "get_price_snapshot",
                        lambda client, epic: PriceSnapshot(epic, 99.9, 100.1, 100.0, "TRADEABLE", None))
    run_shadow_cycle(tmp_path_db := db_path, MagicMock(), _engine(),
                     candidates={"hypothesis_v3cand": {"entry_fn": _no_signal_entry_fn, "assets": ["GOLD"],
                                                        "extras": [], "donchian": False}})
    assert _rows(db_path, "SELECT * FROM shadow_trades") == []


def test_run_shadow_cycle_manages_existing_open_trade(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path, entry=100.0, stop=90.0, tp1=105.0, tp2=110.0)
    import src.shadow_tracking as mod
    crash_candles = [_candle(i, 101, 99, 100) for i in range(219)] + [_candle(219, 100, 80, 85)]
    monkeypatch.setattr(mod, "get_candles", lambda client, epic, resolution, count: crash_candles)
    monkeypatch.setattr(mod, "get_price_snapshot",
                        lambda client, epic: PriceSnapshot(epic, 84.9, 85.1, 85.0, "TRADEABLE", None))

    closed = run_shadow_cycle(db_path, MagicMock(), _engine(),
                              candidates={"hypothesis_v3cand": {"entry_fn": _no_signal_entry_fn, "assets": ["GOLD"],
                                                                 "extras": [], "donchian": False}})

    assert closed == 1
    assert _rows(db_path, "SELECT * FROM shadow_trades")[0]["statut"] == "ferme"


def test_run_shadow_cycle_skips_asset_on_candle_fetch_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    import src.shadow_tracking as mod
    monkeypatch.setattr(mod, "get_candles", lambda *a, **k: (_ for _ in ()).throw(CapitalApiError("503")))
    run_shadow_cycle(db_path, MagicMock(), _engine(),
                     candidates={"hypothesis_v3cand": {"entry_fn": _fake_signal_entry_fn, "assets": ["GOLD"],
                                                        "extras": [], "donchian": False}})
    assert _rows(db_path, "SELECT * FROM shadow_trades") == []


def test_run_shadow_cycle_skips_on_snapshot_failure_for_new_signal(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    import src.shadow_tracking as mod
    candles = [_candle(i, 101, 99, 100) for i in range(220)]
    monkeypatch.setattr(mod, "get_candles", lambda client, epic, resolution, count: candles)
    monkeypatch.setattr(mod, "get_price_snapshot", lambda *a, **k: (_ for _ in ()).throw(CapitalApiError("503")))
    run_shadow_cycle(db_path, MagicMock(), _engine(),
                     candidates={"hypothesis_v3cand": {"entry_fn": _fake_signal_entry_fn, "assets": ["GOLD"],
                                                        "extras": [], "donchian": False}})
    assert _rows(db_path, "SELECT * FROM shadow_trades") == []


def test_run_shadow_cycle_skips_on_snapshot_failure_for_open_trade(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _open(db_path)
    import src.shadow_tracking as mod
    candles = [_candle(i, 101, 99, 100) for i in range(220)]
    monkeypatch.setattr(mod, "get_candles", lambda client, epic, resolution, count: candles)
    monkeypatch.setattr(mod, "get_price_snapshot", lambda *a, **k: (_ for _ in ()).throw(CapitalApiError("503")))
    closed = run_shadow_cycle(db_path, MagicMock(), _engine(),
                              candidates={"hypothesis_v3cand": {"entry_fn": _no_signal_entry_fn, "assets": ["GOLD"],
                                                                 "extras": [], "donchian": False}})
    assert closed == 0
    assert _rows(db_path, "SELECT * FROM shadow_trades")[0]["statut"] == "ouvert"


def test_run_shadow_cycle_h2_style_candidate_needs_extra_resolutions(tmp_path, monkeypatch):
    """Une candidate multi-TF (H2) n'ouvre rien si une résolution
    supplémentaire est indisponible — jamais un signal deviné."""
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    import src.shadow_tracking as mod
    own = [_candle(i, 101, 99, 100) for i in range(220)]
    calls = {"n": 0}

    def fake_get_candles(client, epic, resolution, count):
        calls["n"] += 1
        return own if resolution == "HOUR" else []  # extras indisponibles

    def fake_entry(asset, m15, h1, h4):
        raise AssertionError("ne doit jamais être appelé sans les résolutions supplémentaires")

    monkeypatch.setattr(mod, "get_candles", fake_get_candles)
    run_shadow_cycle(db_path, MagicMock(), _engine(),
                     candidates={"hypothesis2_v3cand": {"entry_fn": fake_entry, "assets": ["GOLD"],
                                                         "extras": ["HOUR_4", "DAY"], "donchian": False}})
    assert _rows(db_path, "SELECT * FROM shadow_trades") == []


# ---------------------------------------------------------------------------
# SHADOW_COUPLES — Partie 4 (08/10/2026) : tous les couples hypothèse_v2 x
# actif de la liste blanche, étiquette "_shadow_couples".
# ---------------------------------------------------------------------------

def test_shadow_couples_covers_every_whitelist_asset_per_hypothesis():
    from src.asset_whitelist import ASSET_WHITELIST
    from src.shadow_tracking import SHADOW_COUPLES

    assert len(SHADOW_COUPLES) == 5
    for source, cfg in SHADOW_COUPLES.items():
        assert source.endswith("_shadow_couples")
        assert set(cfg["assets"]) == set(ASSET_WHITELIST.keys())
        assert callable(cfg["entry_fn"])


def test_shadow_couples_h1_runs_end_to_end_without_broker_writes(tmp_path, monkeypatch):
    """Preuve par test (pas seulement par lecture du code) qu'un couple
    réel de SHADOW_COUPLES (H1 x GOLD, la stratégie _v2 ACTUELLEMENT
    déployée) ne déclenche jamais de méthode d'écriture broker, même
    pour un actif dont le plafond de cluster bloquerait l'exécution
    réelle (jamais consulté ici, comme pour SHADOW_CANDIDATES)."""
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    import src.shadow_tracking as mod
    from src.shadow_tracking import SHADOW_COUPLES

    flat_candles = [_candle(i, 101, 99, 100) for i in range(220)]
    monkeypatch.setattr(mod, "get_candles", lambda client, epic, resolution, count: flat_candles)
    monkeypatch.setattr(mod, "get_price_snapshot",
                        lambda client, epic: PriceSnapshot(epic, 99.9, 100.1, 100.0, "TRADEABLE", None))
    client = MagicMock()

    cfg = SHADOW_COUPLES["hypothesis_v2_shadow_couples"]
    run_shadow_cycle(db_path, client, _engine(),
                     candidates={"hypothesis_v2_shadow_couples": {**cfg, "assets": ["GOLD"]}})

    for forbidden in ("place_limit_order", "open_position", "close_position", "update_position_stop",
                      "cancel_working_order"):
        assert getattr(client, forbidden).called is False
