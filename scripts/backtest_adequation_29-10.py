"""Backtest hypothèse x actif (29/10/2026) — protocole : docs/protocole_backtest_2026-10-29.md.
Rejoue les stratégies de PRODUCTION (`*_strategy_v2.evaluate_entry`) via `backtest_engine.replay_hypothesis`
(entry_mode="level_fill"), sans aucun appel réseau, puis applique `src/adequation_decision`.

Usage :
    python scripts/backtest_adequation_29-10.py replay [--jobs 4]      # écrit data/backtest_29-10/trades_*.json
    python scripts/backtest_adequation_29-10.py lookahead             # contrôle anti look-ahead sur données réelles
    python scripts/backtest_adequation_29-10.py analyse               # écrit data/backtest_29-10/resultats.json
"""
import json
import multiprocessing
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_v2  # noqa: E402
import src.hypothesis2_strategy_v2 as h2_v2  # noqa: E402
import src.hypothesis3_strategy_v2 as h3_v2  # noqa: E402
import src.hypothesis4_strategy_v2 as h4_v2  # noqa: E402
import src.hypothesis5_strategy_v2 as h5_v2  # noqa: E402
from src.asset_whitelist import ASSET_WHITELIST  # noqa: E402
from src.entry_filters import spread_filter_rejection  # noqa: E402
from src.backtest_engine import bar_from_raw, replay_hypothesis  # noqa: E402
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import StopRefusalModel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "backtest_29-10"
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
ENVELOPE = 500.0
MIN_DATE = "2019-01-01"
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}

# Amendement n°1 (variante B) : distance minimale du stop garanti, en % du prix d'entrée (minimum effectif
# observé sur les trades live / marge 1,01 — voir docs/protocole_backtest_2026-10-29.md §9).
MIN_GUARANTEED_STOP_PCT = {
    "GOLD": 0.25, "EURUSD": 0.25, "BTCUSD": 0.50, "ETHUSD": 0.50,
    "US100": 0.10, "US30": 0.10, "GBPUSD": 0.10, "USDJPY": 0.10, "CHFJPY": 0.10,
}
GUARANTEED_STOP_SAFETY_MARGIN = 1.01  # = executor.GUARANTEED_STOP_SAFETY_MARGIN


def production_precheck(asset, direction, entry, stop, bar):
    """Pré-contrôles d'entrée de la production : stop élargi au minimum broker (jamais resserré), puis refus
    si spread/stop > 0,10 (`entry_filters.spread_filter_rejection`, bid/ask d'ouverture de la bougie d'exécution)."""
    min_distance = entry * MIN_GUARANTEED_STOP_PCT[asset] / 100.0 * GUARANTEED_STOP_SAFETY_MARGIN
    distance = abs(entry - stop)
    if distance < min_distance:
        stop = round(entry - min_distance, 8) if direction == "long" else round(entry + min_distance, 8)
    if spread_filter_rejection(bar.open_bid, bar.open_ask, entry, stop) is not None:
        return None
    return stop


HYP = {
    "H1": {"module": h1_v2, "extras": [], "donchian": False},
    "H2": {"module": h2_v2, "extras": ["HOUR_4", "DAY"], "donchian": False},
    "H3": {"module": h3_v2, "extras": [], "donchian": False},
    "H4": {"module": h4_v2, "extras": [], "donchian": False},
    "H5": {"module": h5_v2, "extras": ["DAY"], "donchian": True},  # aucun tp1/tp2 (docstring du module)
}


def jobs():
    """CHFJPY : H1 seulement (exclue de H2-H5 le 29/08 ; DAY indisponible avant 2023-12-10)."""
    return [(h, a) for h in HYP for a in ASSETS if not (a == "CHFJPY" and h != "H1")]


def load_bars(asset, resolution):
    raw = json.loads((HIST / f"{asset}_{resolution}.json").read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None and b.time_utc >= MIN_DATE]
    return sorted(bars, key=lambda b: b.time_utc)


def run_replay(hyp, asset, own_bars=None, extras=None, stop_refusal=True, variant="A"):
    cfg = HYP[hyp]
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, ENVELOPE), whitelist=ASSET_WHITELIST)
    own_bars = own_bars if own_bars is not None else load_bars(asset, "HOUR")
    kwargs = {
        "is_donchian_trailing": cfg["donchian"],
        "stop_update_filter": StopRefusalModel() if stop_refusal else None,
        "entry_mode": "level_fill",
        "entry_precheck": production_precheck if variant == "B" else None,
    }
    if cfg["extras"]:
        extras = extras if extras is not None else {r: load_bars(asset, r) for r in cfg["extras"]}
        kwargs.update(
            extra_resolution_bars=extras, own_bar_duration_seconds=3600.0,
            extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in cfg["extras"]},
        )
    return replay_hypothesis(asset, own_bars, cfg["module"].evaluate_entry, engine, ASSET_WHITELIST, ENVELOPE, 0.75, **kwargs)


VARIANT = os.environ.get("BT_VARIANT", "A")  # lu aussi par les processus fils (spawn)


def _job(args):
    hyp, asset = args
    result = run_replay(hyp, asset, variant=VARIANT)
    payload = {
        "hypothesis": hyp, "asset": asset, "orders_placed": result.orders_placed, "orders_unfilled": result.orders_unfilled,
        "orders_prechecked_out": result.orders_prechecked_out,
        "trades": [
            {"entry_time": t.entry_time_utc, "exit_time": t.exit_time_utc, "r": t.r_multiple_total,
             "risk_eur": t.risk_amount_eur, "direction": t.direction}
            for t in result.trades
        ],
    }
    out_dir = OUT if VARIANT == "A" else OUT / VARIANT
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"trades_{hyp}_{asset}.json").write_text(json.dumps(payload), encoding="utf-8")
    return hyp, asset, len(result.trades), result.orders_placed, result.orders_unfilled


def cmd_replay(n_jobs):
    todo = jobs()
    print(f"{len(todo)} rejeux, {n_jobs} processus", flush=True)
    with multiprocessing.Pool(n_jobs) as pool:
        for hyp, asset, n, placed, unfilled in pool.imap_unordered(_job, todo):
            print(f"  {hyp} {asset}: {n} trades, ordres {placed} (non remplis {unfilled})", flush=True)


def cmd_lookahead():
    """Rejeu complet vs rejeu tronqué (barres futures retirées, séries de résolution supérieure laissées
    COMPLÈTES : le moteur ne doit pas les consulter au-delà de la clôture) : les trades clos avant la
    troncature doivent être identiques, pour chaque hypothèse (GBPUSD)."""
    asset = "GBPUSD"
    own = load_bars(asset, "HOUR")
    cut = len(own) - 6000
    cut_time = own[cut].time_utc
    ok_all = True
    for hyp, cfg in HYP.items():
        extras = {r: load_bars(asset, r) for r in cfg["extras"]} if cfg["extras"] else None
        full = run_replay(hyp, asset, own, extras, stop_refusal=False)
        trunc = run_replay(hyp, asset, own[:cut], extras, stop_refusal=False)
        key = lambda t: (t.entry_time_utc, t.exit_time_utc, round(t.r_multiple_total, 10))
        before_full = [key(t) for t in full.trades if t.exit_time_utc < cut_time]
        before_trunc = [key(t) for t in trunc.trades if t.exit_time_utc < cut_time]
        ok = before_full == before_trunc
        ok_all &= ok
        print(f"  {hyp} {asset}: {len(before_full)} trades clos avant {cut_time} -> {'IDENTIQUES' if ok else 'DIFFÉRENTS'}", flush=True)
    print("LOOKAHEAD", "OK" if ok_all else "ECHEC")
    return 0 if ok_all else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if "--variant" in sys.argv:
        os.environ["BT_VARIANT"] = VARIANT = sys.argv[sys.argv.index("--variant") + 1]
    if cmd == "replay":
        jobs_n = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 3
        cmd_replay(jobs_n)
    elif cmd == "lookahead":
        sys.exit(cmd_lookahead())
    elif cmd == "analyse":
        from backtest_adequation_analyse import main as analyse_main  # noqa: E402
        sys.exit(analyse_main())
    else:
        print(__doc__)
        sys.exit(2)
