"""Test automatique anti look-ahead (protocole docs/protocole_backtest_2026-10-29.md §6) :
rejouer sur une série TRONQUÉE doit donner exactement les mêmes trades clos avant la troncature
que le rejeu complet, pour chacune des 5 hypothèses (séries de résolution supérieure laissées
complètes : le moteur ne doit pas les consulter au-delà de la clôture). Données synthétiques."""
import importlib.util
import math
import pathlib
import random
from datetime import datetime, timedelta

import pytest

from src.backtest_engine import HistoricalBar

_spec = importlib.util.spec_from_file_location(
    "bt_adequation", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backtest_adequation_29-10.py")
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def _synthetic_hourly(n=3600, seed=11):
    """Marche aléatoire à régimes (tendances + oscillations), spread fixe, jamais de week-end manquant."""
    rng = random.Random(seed)
    price, drift = 1.30, 0.0
    t0 = datetime(2022, 1, 3)
    bars = []
    for i in range(n):
        if i % 200 == 0:
            drift = rng.choice([-1, 1]) * 0.0004 * rng.random() + 0.0002 * math.sin(i / 50)
        o = price
        c = o + drift + rng.gauss(0, 0.0012)
        hi = max(o, c) + abs(rng.gauss(0, 0.0006))
        lo = min(o, c) - abs(rng.gauss(0, 0.0006))
        price = c
        half = 0.00008
        ts = (t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
        bars.append(HistoricalBar(ts, o - half, o + half, hi - half, hi + half, lo - half, lo + half, c - half, c + half))
    return bars


def _aggregate(bars, hours):
    out = []
    for i in range(0, len(bars) - hours + 1, hours):
        chunk = bars[i:i + hours]
        out.append(HistoricalBar(
            chunk[0].time_utc, chunk[0].open_bid, chunk[0].open_ask,
            max(b.high_bid for b in chunk), max(b.high_ask for b in chunk),
            min(b.low_bid for b in chunk), min(b.low_ask for b in chunk),
            chunk[-1].close_bid, chunk[-1].close_ask,
        ))
    return out


def _key(t):
    return (t.entry_time_utc, t.exit_time_utc, round(t.r_multiple_total, 10))


@pytest.mark.parametrize("hyp", ["H1", "H2", "H3", "H4", "H5"])
def test_truncated_replay_matches_full_replay_before_cut(hyp):
    own = _synthetic_hourly()
    extras = {"HOUR_4": _aggregate(own, 4), "DAY": _aggregate(own, 24)}
    cfg_extras = bt.HYP[hyp]["extras"]
    extras = {k: extras[k] for k in cfg_extras} if cfg_extras else None
    cut = 2800
    cut_time = own[cut].time_utc
    full = bt.run_replay(hyp, "GBPUSD", own, extras, stop_refusal=False)
    trunc = bt.run_replay(hyp, "GBPUSD", own[:cut], extras, stop_refusal=False)
    before_full = [_key(t) for t in full.trades if t.exit_time_utc < cut_time]
    before_trunc = [_key(t) for t in trunc.trades if t.exit_time_utc < cut_time]
    assert before_full == before_trunc


def test_level_fill_order_counts_are_consistent():
    own = _synthetic_hourly(1500, seed=5)
    res = bt.run_replay("H1", "GBPUSD", own, None, stop_refusal=False)
    assert res.orders_placed >= res.orders_unfilled >= 0
    assert len(res.trades) <= res.orders_placed - res.orders_unfilled
