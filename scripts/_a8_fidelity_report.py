"""
_a8_fidelity_report.py — A8 du bilan du 05/10/2026 : écart backtest/démo
AVANT et APRÈS modélisation des deux frictions live (refus de resserrement
de stop, plafond de cluster), sur la dernière fenêtre live couverte par
`data/historical/`.

Lecture seule : une copie de la base (`--db`, par défaut l'instantané local
`data/snapshots/prod_05-10.db`) et `data/historical/`. Aucune bougie de la
fenêtre de confirmation 2023-01-01 → 2024-06-14 n'est chargée : la fenêtre
rejouée commence 90 jours (HOUR) / 200 jours (DAY) avant le 03/09/2026.

Trois mesures :
1. Appariement des signaux live/backtest (même sens, ±2 h), comme
   `_compare_live_vs_backtest_window.py`, sans puis avec modèle de refus
   (un stop non resserré prolonge un trade et décale les signaux suivants).
2. Écart de R sur les trades H5 appariés de la période pré-E2 (seule
   hypothèse dont la sortie live était conforme avant le 25/09) : R live −
   R backtest, sans puis avec modèle de refus.
3. Plafond de cluster : part des trades backtest que le plafond aurait
   bloqués (toutes hypothèses rejouées ensemble), comparée à la part des
   épisodes de signal live bloqués à 100 % par ce plafond sur la même
   fenêtre.
"""

import argparse
import json
import sqlite3
import statistics
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_mod  # noqa: E402
import src.hypothesis2_strategy_v2 as h2_mod  # noqa: E402
import src.hypothesis3_strategy_v2 as h3_mod  # noqa: E402
import src.hypothesis4_strategy_v2 as h4_mod  # noqa: E402
import src.hypothesis5_strategy_v2 as h5_mod  # noqa: E402
from src.asset_whitelist import ASSET_WHITELIST  # noqa: E402
from src.backtest_engine import bar_from_raw, replay_hypothesis  # noqa: E402
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import PortfolioTrade, StopRefusalModel, apply_cluster_cap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
WINDOW_START = "2026-09-03T00:00:00"
WINDOW_END = "2026-09-25T18:00:00"   # dernière bougie HOUR disponible
SEALED_START, SEALED_END = "2023-01-01", "2024-06-15"
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}
TOL = timedelta(hours=2)
ENVELOPE = 500.0
PROVISIONAL_RISK_EUR = ENVELOPE * 4.0 / 100.0  # taux boosté × enveloppe, comme executor.open_signal

HYP = {
    "hypothesis_v2": (h1_mod, [], False, {}),
    "hypothesis2_v2": (h2_mod, ["HOUR_4", "DAY"], False,
                       {"EMA_PERIOD": 20, "RSI_THRESHOLD": 55.0, "N_TF": 3, "SCORE_THRESHOLD": 1.0}),
    "hypothesis3_v2": (h3_mod, [], False, {}),
    "hypothesis4_v2": (h4_mod, [], False, {}),
    "hypothesis5_v2": (h5_mod, ["DAY"], True, {"TSMOM_FILTER_ENABLED": False}),
}


@contextmanager
def override(module, attrs):
    saved = {k: getattr(module, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(module, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(module, k, v)


_cache = {}


def bars(asset, res, start, end):
    if (asset, res) not in _cache:
        raw = json.loads((HIST / f"{asset}_{res}.json").read_text(encoding="utf-8"))
        _cache[(asset, res)] = [b for b in (bar_from_raw(p) for p in raw) if b is not None]
    out = [b for b in _cache[(asset, res)] if start <= b.time_utc <= end]
    assert not any(SEALED_START <= b.time_utc < SEALED_END for b in out), "fenêtre de confirmation chargée"
    return out


def dt(ts):
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def norm(ts):
    return datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def replay(source, asset, refusal):
    module, extras, donchian, attrs = HYP[source]
    own_from = (dt(WINDOW_START) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    extra_from = (dt(WINDOW_START) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S")
    kwargs = {"is_donchian_trailing": donchian, "stop_update_filter": StopRefusalModel() if refusal else None}
    if extras:
        kwargs.update(
            extra_resolution_bars={r: bars(asset, r, extra_from, WINDOW_END) for r in extras},
            own_bar_duration_seconds=3600.0, extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in extras},
        )
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, ENVELOPE), whitelist=ASSET_WHITELIST)
    with override(module, attrs):
        result = replay_hypothesis(asset, bars(asset, "HOUR", own_from, WINDOW_END), module.evaluate_entry,
                                   engine, ASSET_WHITELIST, ENVELOPE, 0.75, **kwargs)
    return [t for t in result.trades if WINDOW_START <= t.signal_time_utc <= WINDOW_END]


def live_trades(conn, source):
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, statut, r_multiple_total FROM trades WHERE source = ?", (source,),
    ).fetchall()
    return [(a, d, norm(o), s, r) for a, d, o, s, r in rows if WINDOW_START <= norm(o) <= WINDOW_END]


def live_blocked_share(conn, source):
    rows = conn.execute(
        "SELECT s.actif, s.sens, s.created_at, "
        "(SELECT reason FROM risk_decisions d WHERE d.signal_id = s.id ORDER BY d.id DESC LIMIT 1), "
        "EXISTS(SELECT 1 FROM trades t WHERE t.signal_id = s.id) "
        "FROM signals s WHERE s.source = ? ORDER BY s.actif, s.sens, s.created_at", (source,),
    ).fetchall()
    episodes = []
    for asset, sens, created, reason, has_trade in rows:
        t = norm(created)
        if not (WINDOW_START <= t <= WINDOW_END):
            continue
        if episodes and episodes[-1]["k"] == (asset, sens) and dt(t) - episodes[-1]["last"] <= timedelta(hours=3):
            episodes[-1]["last"] = dt(t)
            episodes[-1]["reasons"].append(reason)
            episodes[-1]["traded"] |= bool(has_trade)
        else:
            episodes.append({"k": (asset, sens), "last": dt(t), "reasons": [reason], "traded": bool(has_trade)})
    blocked = sum(1 for e in episodes if not e["traded"] and all(r == "cluster_exposure_cap" for r in e["reasons"]))
    return blocked, len(episodes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "snapshots" / "prod_05-10.db"))
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    portfolio = {False: [], True: []}
    print(f"Fenêtre {WINDOW_START} -> {WINDOW_END} (configurations live pré-E1/pré-E2)\n")
    print("1. Appariement des signaux (part des signaux live retrouvés) et 2. écart de R H5")
    for source in HYP:
        live = live_trades(conn, source)
        live_signals = sorted({(a, d, t[:13]) for a, d, t, _, _ in live})
        line = f"  {source:15s} live={len(live_signals):3d}"
        for refusal in (False, True):
            bt_all = []
            for asset in ASSETS:
                for t in replay(source, asset, refusal):
                    bt_all.append(t)
                    portfolio[refusal].append(PortfolioTrade(source, asset, t.entry_time_utc, t.exit_time_utc,
                                                             t.risk_amount_eur, t.r_multiple_total))
            matched = sum(1 for a, d, h in live_signals if any(
                b.asset == a and b.direction == d and abs(dt(b.signal_time_utc) - dt(h + ":00:00")) <= TOL for b in bt_all))
            tag = "AVEC refus" if refusal else "sans refus"
            pct = 100 * matched / len(live_signals) if live_signals else 0
            line += f" | {tag}: backtest={len(bt_all):3d} appariés={matched:3d} ({pct:.0f}%)"
            if source == "hypothesis5_v2":
                diffs = []
                for a, d, t, statut, r in live:
                    if statut != "ferme" or r is None:
                        continue
                    cands = [b for b in bt_all if b.asset == a and b.direction == d
                             and abs(dt(b.signal_time_utc) - dt(t)) <= TOL]
                    if cands:
                        diffs.append(r - min(cands, key=lambda b: abs(dt(b.signal_time_utc) - dt(t))).r_multiple_total)
                if diffs:
                    line += (f" [H5 R live−backtest sur {len(diffs)} paires : moyenne {statistics.mean(diffs):+.3f}, "
                             f"|écart| moyen {statistics.mean(abs(x) for x in diffs):.3f}]")
        print(line)

    print("\n3. Plafond de cluster : part bloquée")
    for refusal in (False, True):
        kept, blocked = apply_cluster_cap(portfolio[refusal], PROVISIONAL_RISK_EUR)
        tag = "AVEC refus" if refusal else "sans refus"
        for source in HYP:
            n_all = sum(1 for t in portfolio[refusal] if t.source == source)
            n_blk = sum(1 for t in blocked if t.source == source)
            print(f"  backtest {tag} {source:15s} : {n_blk}/{n_all} trades bloqués "
                  f"({100 * n_blk / n_all if n_all else 0:.0f}%)")
    for source in HYP:
        b, n = live_blocked_share(conn, source)
        print(f"  live {source:15s} : {b}/{n} épisodes de signal bloqués à 100 % ({100 * b / n if n else 0:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
