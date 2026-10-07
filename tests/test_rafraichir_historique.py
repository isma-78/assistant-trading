"""Tests de scripts/rafraichir_historique.py (Étape 1, mandat 08/10/2026).
Broker entièrement mocké (FakeClient) — aucun appel réseau."""

import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest
import requests

import src.retry as retry_module
from src.capital_client import CapitalApiError

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "rafraichir_historique.py")
_spec = importlib.util.spec_from_file_location("rafraichir_historique", _PATH)
rh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rh)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(retry_module.time, "sleep", lambda *_a, **_k: None)


def _make_db(path, rule_changes_rows=None, system_state_rows=None, rejected_count=0, omit_tables=False):
    conn = sqlite3.connect(path)
    if omit_tables:
        conn.close()
        return
    conn.execute(
        "CREATE TABLE rule_changes (variable TEXT, ajustement_propose TEXT, statut TEXT, applied_at TEXT)"
    )
    conn.execute("CREATE TABLE system_state (key TEXT, value TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE trades (annulation_motif TEXT, ouvert_at TEXT)")
    for row in rule_changes_rows or []:
        conn.execute("INSERT INTO rule_changes VALUES (?, ?, ?, ?)", row)
    for row in system_state_rows or []:
        conn.execute("INSERT INTO system_state VALUES (?, ?, ?)", row)
    for _ in range(rejected_count):
        conn.execute("INSERT INTO trades VALUES ('rate_limit_429', '2099-01-01T00:00:00')")
    conn.commit()
    conn.close()


def _candle(ts, bid=1.0, ask=1.0002):
    return {
        "snapshotTimeUTC": ts,
        "openPrice": {"bid": bid, "ask": ask},
        "closePrice": {"bid": bid, "ask": ask},
        "highPrice": {"bid": bid, "ask": ask},
        "lowPrice": {"bid": bid, "ask": ask},
    }


class FakeClient:
    def __init__(self, pages=None, error=None):
        self.pages = pages or []
        self.error = error
        self.calls = []
        self.logged_in = False
        self.switched = None

    def login(self):
        self.logged_in = True
        return {}

    def switch_account(self, account_id):
        self.switched = account_id
        return {}

    def get(self, path, params=None):
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        index = len(self.calls) - 1
        if index < len(self.pages):
            return {"prices": self.pages[index]}
        return {"prices": []}


class _FakeConfig:
    def __init__(self, db_path, environment="demo"):
        self.capital_environment = environment
        self.db_path = db_path
        self.capital_api_key = "k"
        self.capital_identifier = "i"
        self.capital_api_password = "p"
        self.capital_account_id = "acc"


# --- get_deployed_combinations -----------------------------------------------

def test_get_deployed_combinations_defaults_to_hour(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)
    combos = rh.get_deployed_combinations(db_path)
    assert ("GOLD", "HOUR") in combos
    assert ("GOLD", "HOUR_4") in combos
    assert ("GOLD", "DAY") in combos
    assert len(combos) == 27


def test_get_deployed_combinations_reads_active_override(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, rule_changes_rows=[
        ("H3_v2.resolution_entree", "HOUR_4", "applique", "2026-10-01T00:00:00"),
    ])
    combos = rh.get_deployed_combinations(db_path)
    assert ("GOLD", "HOUR_4") in combos


# --- load_tick_sizes ----------------------------------------------------------

def test_load_tick_sizes_real_file_has_chfjpy_fallback():
    sizes = rh.load_tick_sizes()
    assert sizes["USDJPY"] == pytest.approx(0.001)
    assert sizes["CHFJPY"] == sizes["USDJPY"]


def test_load_tick_sizes_no_fallback_source_no_chfjpy(tmp_path):
    specs = {"assets": {"GOLD": {"primary": {"epic": "GOLD", "minStepDistance_value": 0.01}}}}
    path = tmp_path / "specs.json"
    path.write_text(json.dumps(specs), encoding="utf-8")
    sizes = rh.load_tick_sizes(path)
    assert sizes == {"GOLD": 0.01}
    assert "CHFJPY" not in sizes


def test_load_tick_sizes_skips_entries_without_tick(tmp_path):
    specs = {"assets": {"X": {"primary": {"epic": "X"}}, "Y": {}}}
    path = tmp_path / "specs.json"
    path.write_text(json.dumps(specs), encoding="utf-8")
    sizes = rh.load_tick_sizes(path)
    assert sizes == {}


# --- executors_rate_limited ----------------------------------------------------

def test_executors_rate_limited_no_signal(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)
    assert rh.executors_rate_limited(db_path, "2026-10-07T00:00:00") == ""


def test_executors_rate_limited_streak(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, system_state_rows=[("api_error_streak:x", "1", "2026-10-07T01:00:00")])
    reason = rh.executors_rate_limited(db_path, "2026-10-07T00:00:00")
    assert "erreurs API" in reason


def test_executors_rate_limited_rejected(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, rejected_count=2)
    reason = rh.executors_rate_limited(db_path, "2026-10-07T00:00:00")
    assert "2 placement" in reason


def test_executors_rate_limited_missing_tables(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, omit_tables=True)
    assert rh.executors_rate_limited(db_path, "2026-10-07T00:00:00") == ""


# --- check_overlap_integrity ---------------------------------------------------

def test_overlap_integrity_no_common_timestamp():
    ok, detail = rh.check_overlap_integrity([_candle("2026-10-01T00:00:00")], [_candle("2026-10-02T00:00:00")], 0.01)
    assert ok and detail["n_overlap"] == 0


def test_overlap_integrity_within_tolerance_even_count():
    existing = [_candle("2026-10-01T00:00:00", 1.0, 1.0002), _candle("2026-10-01T01:00:00", 1.0, 1.0002)]
    fresh = [_candle("2026-10-01T00:00:00", 1.0, 1.0002), _candle("2026-10-01T01:00:00", 1.0, 1.0004)]
    ok, detail = rh.check_overlap_integrity(existing, fresh, tick_size=0.001)
    assert ok and detail["n_overlap"] == 2


def test_overlap_integrity_exceeds_tolerance():
    existing = [_candle("2026-10-01T00:00:00", 1.0, 1.0002)]
    fresh = [_candle("2026-10-01T00:00:00", 1.0, 1.1)]
    ok, detail = rh.check_overlap_integrity(existing, fresh, tick_size=0.001)
    assert not ok and detail["median_diff"] > 0.001


def test_overlap_integrity_skips_missing_spread_fields():
    existing = [_candle("2026-10-01T00:00:00")]
    broken = {"snapshotTimeUTC": "2026-10-01T00:00:00", "closePrice": {"bid": None, "ask": None}}
    ok, detail = rh.check_overlap_integrity(existing, [broken], 0.001)
    assert ok and detail["n_overlap"] == 0


# --- fetch_window ---------------------------------------------------------------

def test_fetch_window_single_page():
    client = FakeClient(pages=[[_candle("2026-10-06T00:00:00")]])
    budget = rh.CallBudget(max_calls=10)
    sleeps = []
    points = rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 1),
                              budget, sleep_fn=sleeps.append)
    assert len(points) == 1
    assert budget.used == 1
    assert sleeps == []  # une seule page, jamais de pause


def test_fetch_window_multiple_pages(monkeypatch):
    monkeypatch.setattr(rh, "MAX_BARS_PER_REQUEST", 2)
    client = FakeClient(pages=[[_candle("2026-10-06T00:00:00")], [_candle("2026-10-06T02:00:00")]])
    budget = rh.CallBudget(max_calls=10)
    sleeps = []
    points = rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 4),
                              budget, sleep_fn=sleeps.append)
    assert len(points) == 2
    assert budget.used == 2
    assert sleeps == [rh.MIN_PAUSE_SECONDS]  # une pause entre les 2 pages


def test_fetch_window_429_aborts():
    client = FakeClient(error=CapitalApiError("error.too-many.requests"))
    budget = rh.CallBudget(max_calls=10)
    with pytest.raises(rh.RateLimitAbort):
        rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 1), budget)


def test_fetch_window_not_found_breaks_without_error():
    client = FakeClient(error=CapitalApiError("error.prices.not-found"))
    budget = rh.CallBudget(max_calls=10)
    points = rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 1), budget)
    assert points == []


def test_fetch_window_other_api_error_propagates():
    client = FakeClient(error=CapitalApiError("error.validation.something"))
    budget = rh.CallBudget(max_calls=10)
    with pytest.raises(CapitalApiError):
        rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 1), budget)


def test_fetch_window_request_exception_propagates():
    client = FakeClient(error=requests.exceptions.ConnectionError("boom"))
    budget = rh.CallBudget(max_calls=10)
    with pytest.raises(requests.exceptions.ConnectionError):
        rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 1), budget)


def test_fetch_window_budget_exhausted():
    client = FakeClient(pages=[[_candle("2026-10-06T00:00:00")]])
    budget = rh.CallBudget(max_calls=0)
    with pytest.raises(rh.BudgetExhausted):
        rh.fetch_window(client, "GOLD", "HOUR", datetime(2026, 10, 6), datetime(2026, 10, 6, 1), budget)


# --- merge --------------------------------------------------------------------

def test_merge_appends_only_new_closed_candles():
    existing = [_candle("2026-10-06T00:00:00")]
    fresh = [
        _candle("2026-10-06T00:00:00"),  # déjà présente -> ignorée
        _candle("2026-10-06T01:00:00"),  # nouvelle, close -> ajoutée
        _candle("2026-10-06T05:00:00"),  # en formation -> exclue
    ]
    merged = rh.merge(existing, fresh, last=datetime(2026, 10, 6, 0), step=timedelta(hours=1),
                       now=datetime(2026, 10, 6, 4))
    assert [p["snapshotTimeUTC"] for p in merged] == ["2026-10-06T00:00:00", "2026-10-06T01:00:00"]


# --- refresh_combination --------------------------------------------------------

def _write_history(tmp_path, asset, resolution, candles):
    path = tmp_path / f"{asset}_{resolution}.json"
    path.write_text(json.dumps(candles), encoding="utf-8")
    return path


def test_refresh_combination_sealed_window_violation(tmp_path):
    _write_history(tmp_path, "GOLD", "HOUR", [_candle("2024-01-01T00:00:00")])
    client = FakeClient()
    budget = rh.CallBudget(max_calls=10)
    with pytest.raises(rh.SealedWindowViolation):
        rh.refresh_combination(client, "GOLD", "HOUR", datetime(2026, 10, 7), budget,
                                {"GOLD": 0.01}, historical_dir=tmp_path)


def test_refresh_combination_writes_new_candles(tmp_path):
    _write_history(tmp_path, "GOLD", "HOUR", [_candle("2026-10-06T00:00:00", 1.0, 1.01)])
    client = FakeClient(pages=[[
        _candle("2026-10-06T00:00:00", 1.0, 1.01),  # chevauchement identique -> intègre
        _candle("2026-10-06T01:00:00", 1.0, 1.01),
    ]])
    budget = rh.CallBudget(max_calls=10)
    result = rh.refresh_combination(client, "GOLD", "HOUR", datetime(2026, 10, 6, 2), budget,
                                     {"GOLD": 0.01}, historical_dir=tmp_path, sleep_fn=lambda *_: None)
    assert result.status == "ok"
    assert result.bougies_apres == 2
    written = json.loads((tmp_path / "GOLD_HOUR.json").read_text(encoding="utf-8"))
    assert len(written) == 2


def test_refresh_combination_up_to_date(tmp_path):
    _write_history(tmp_path, "GOLD", "HOUR", [_candle("2026-10-06T00:00:00")])
    client = FakeClient(pages=[[_candle("2026-10-06T00:00:00")]])
    budget = rh.CallBudget(max_calls=10)
    result = rh.refresh_combination(client, "GOLD", "HOUR", datetime(2026, 10, 6, 1), budget,
                                     {"GOLD": 0.01}, historical_dir=tmp_path)
    assert result.status == "up_to_date"


def test_refresh_combination_abandoned_on_integrity_failure(tmp_path):
    _write_history(tmp_path, "GOLD", "HOUR", [_candle("2026-10-06T00:00:00", 1.0, 1.01)])
    client = FakeClient(pages=[[_candle("2026-10-06T00:00:00", 1.0, 9.0)]])  # spread très différent
    budget = rh.CallBudget(max_calls=10)
    result = rh.refresh_combination(client, "GOLD", "HOUR", datetime(2026, 10, 6, 1), budget,
                                     {"GOLD": 0.01}, historical_dir=tmp_path)
    assert result.status == "abandoned_integrity"
    untouched = json.loads((tmp_path / "GOLD_HOUR.json").read_text(encoding="utf-8"))
    assert len(untouched) == 1


def test_refresh_combination_unknown_tick_size(tmp_path):
    _write_history(tmp_path, "GOLD", "HOUR", [_candle("2026-10-06T00:00:00")])
    client = FakeClient(pages=[[_candle("2026-10-06T01:00:00")]])
    budget = rh.CallBudget(max_calls=10)
    result = rh.refresh_combination(client, "GOLD", "HOUR", datetime(2026, 10, 6, 2), budget,
                                     {}, historical_dir=tmp_path)
    assert result.status == "error"


# --- main --------------------------------------------------------------------

def test_main_refuses_outside_demo(tmp_path):
    config = _FakeConfig(str(tmp_path / "db.sqlite"), environment="live")
    assert rh.main(config=config) == 1


def test_main_happy_path(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)
    config = _FakeConfig(db_path)

    hist_dir = tmp_path / "historical"
    hist_dir.mkdir()
    for asset, resolution in rh.get_deployed_combinations(db_path):
        _write_history(hist_dir, asset, resolution, [_candle("2026-10-06T00:00:00")])

    client = FakeClient(pages=[[_candle("2026-10-06T01:00:00")]])
    code = rh.main(config=config, client_factory=lambda cfg: client, sleep_fn=lambda *_: None,
                    historical_dir=hist_dir)
    assert code == 0
    assert client.logged_in
    assert client.switched == "acc"


def test_main_stops_on_executor_rate_limit(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path, rejected_count=1)
    config = _FakeConfig(db_path)
    hist_dir = tmp_path / "historical"
    hist_dir.mkdir()
    for asset, resolution in rh.get_deployed_combinations(db_path):
        _write_history(hist_dir, asset, resolution, [_candle("2026-10-06T00:00:00")])
    client = FakeClient()
    code = rh.main(config=config, client_factory=lambda cfg: client, historical_dir=hist_dir)
    assert code == 2


def test_main_stops_on_429(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)
    config = _FakeConfig(db_path)
    hist_dir = tmp_path / "historical"
    hist_dir.mkdir()
    for asset, resolution in rh.get_deployed_combinations(db_path):
        _write_history(hist_dir, asset, resolution, [_candle("2026-10-06T00:00:00")])
    client = FakeClient(error=CapitalApiError("error.too-many.requests"))
    code = rh.main(config=config, client_factory=lambda cfg: client, historical_dir=hist_dir)
    assert code == 2


def test_main_stops_on_budget_exhaustion(tmp_path, monkeypatch):
    db_path = str(tmp_path / "db.sqlite")
    _make_db(db_path)
    config = _FakeConfig(db_path)
    hist_dir = tmp_path / "historical"
    hist_dir.mkdir()
    for asset, resolution in rh.get_deployed_combinations(db_path):
        _write_history(hist_dir, asset, resolution, [_candle("2026-10-06T00:00:00")])
    monkeypatch.setattr(rh, "MAX_CALLS", 0)
    client = FakeClient(pages=[[_candle("2026-10-06T01:00:00")]])
    code = rh.main(config=config, client_factory=lambda cfg: client, historical_dir=hist_dir)
    assert code == 3
