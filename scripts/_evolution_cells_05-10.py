"""
_evolution_cells_05-10.py — Étape 4.1/4.2 du mandat du 05/10/2026 :
calcul de la matrice hypothèse × actif sur la découverte 2019-01-01 →
2022-12-31 et walk-forward, selon le protocole pré-enregistré
`docs/PROTOCOLE_EVOLUTION_05-10.md` (commit 415dbb5, écrit avant ce script).

Configuration rejouée = configuration RÉELLEMENT déployée au 05/10/2026 :
résolution HOUR pour les 5 ; H2 + HOUR_4/DAY aux valeurs de grille par
défaut (aucun override, combo retiré le 25/09) ; H5 + DAY, filtre TSMOM
d'E2 actif, trailing Donchian ; H3/H4 sans confirmation de régime.

Garde-fous : aucune bougie ≥ 2023-01-01 n'est chargée (fenêtre de
confirmation scellée), aucune < 2019-01-01. Lecture seule de
`data/historical/`. Trades sauvegardés dans `data/snapshots/` (non commité).

Usage : python scripts/_evolution_cells_05-10.py [--replay]
  --replay : relance les rejeux (sinon relit les trades sauvegardés).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_mod  # noqa: E402
import src.hypothesis2_strategy_v2 as h2_mod  # noqa: E402
import src.hypothesis3_strategy_v2 as h3_mod  # noqa: E402
import src.hypothesis4_strategy_v2 as h4_mod  # noqa: E402
import src.hypothesis5_strategy_v2 as h5_mod  # noqa: E402
from src.asset_cell_selection import CellTrade, classify_cells, walk_forward  # noqa: E402
from src.asset_whitelist import ASSET_WHITELIST  # noqa: E402
from src.backtest_engine import bar_from_raw, replay_hypothesis  # noqa: E402
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import StopRefusalModel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "snapshots"
START, END = "2019-01-01", "2023-01-01"  # [début, fin[ — fin = début de la fenêtre scellée
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
EXCLUDED = {"H1": [], "H2": ["CHFJPY"], "H3": ["CHFJPY"], "H4": ["CHFJPY"], "H5": ["CHFJPY"]}
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}
HYP = {
    "H1": (h1_mod, [], False),
    "H2": (h2_mod, ["HOUR_4", "DAY"], False),
    "H3": (h3_mod, [], False),
    "H4": (h4_mod, [], False),
    "H5": (h5_mod, ["DAY"], True),
}


def load(asset, res):
    raw = json.loads((HIST / f"{asset}_{res}.json").read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None and START <= b.time_utc < END]
    assert all(START <= b.time_utc < END for b in bars)
    return bars


def run_replays(refusal: bool):
    trades = []
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, 500.0), whitelist=ASSET_WHITELIST)
    for hyp, (module, extras, donchian) in HYP.items():
        for asset in ASSETS:
            kwargs = {"is_donchian_trailing": donchian, "stop_update_filter": StopRefusalModel() if refusal else None}
            if extras:
                kwargs.update(extra_resolution_bars={r: load(asset, r) for r in extras},
                              own_bar_duration_seconds=3600.0,
                              extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in extras})
            result = replay_hypothesis(asset, load(asset, "HOUR"), module.evaluate_entry, engine,
                                       ASSET_WHITELIST, 500.0, 0.75, **kwargs)
            for t in result.trades:
                trades.append({"hypothesis": hyp, "asset": asset, "entry_time": t.entry_time_utc,
                               "exit_time": t.exit_time_utc, "r": t.r_multiple_total})
            print(f"  [{'refus' if refusal else 'sans'}] {hyp} {asset}: {len(result.trades)} trades", flush=True)
    return trades


def report(trades_raw, title):
    print(f"\n===== {title} =====")
    trades = [CellTrade(t["hypothesis"], t["asset"], t["entry_time"], t["r"]) for t in trades_raw]
    out = {}
    for hyp in HYP:
        ht = [t for t in trades if t.hypothesis == hyp]
        cells = classify_cells(ht, ASSETS, (START, END), (("2019-01-01", "2021-01-01"), ("2021-01-01", "2023-01-01")),
                               EXCLUDED[hyp])
        wf = walk_forward(hyp, ht, ASSETS, EXCLUDED[hyp])
        all_mean = sum(t.r for t in ht) / len(ht) if ht else float("nan")
        print(f"\n{hyp} : {len(ht)} trades, espérance hypothèse {all_mean:+.4f}")
        for asset, c in cells.items():
            fmt = lambda v: "   -   " if v is None else f"{v:+.4f}"
            print(f"  {asset:7s} n={c.n:5d} brute={fmt(c.mean_r)} contractée={fmt(c.contracted)} "
                  f"19-20={fmt(c.mean_half1)} 21-22={fmt(c.mean_half2)} -> {c.status} ({c.reason})")
        fmt = lambda v: "n/a" if v is None else f"{v:+.4f}"
        print(f"  WALK-FORWARD : D(a)={fmt(wf.diff_a)} D(b)={fmt(wf.diff_b)} D poolé={fmt(wf.pooled_diff)} "
              f"borne basse m=5={fmt(wf.lower_bound)} (tirages invalides {wf.invalid_resamples}) "
              f"n_test retenues={wf.n_retained_test} non retenues={wf.n_other_test} -> "
              f"{'VALIDÉE' if wf.validated else 'NON VALIDÉE'} ({wf.reason})")
        out[hyp] = {"cells": {a: c.__dict__ for a, c in cells.items()}, "walk_forward": wf.__dict__,
                    "n": len(ht), "mean": all_mean}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for refusal in (True, False):
        path = OUT / f"cells_trades_{'refus' if refusal else 'sans_refus'}.json"
        if args.replay or not path.exists():
            path.write_text(json.dumps(run_replays(refusal)), encoding="utf-8")
        trades = json.loads(path.read_text(encoding="utf-8"))
        title = ("PRINCIPAL — avec modèle de refus de resserrement (A8)" if refusal
                 else "INFORMATION — sans modèle de refus (ne décide de rien)")
        results["refus" if refusal else "sans_refus"] = report(trades, title)
    (OUT / "cells_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
