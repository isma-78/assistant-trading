"""
_exit_structure_comparison.py — Chantier "structure de sortie" (28/08/2026,
voir docs/DECISIONS.md). Compare, pour H1/H3/H4/H5, MEMES signaux
d'entree (declencheur + stop initial INCHANGES), sous deux structures de
sortie :
  A. 50/30/20 (TP1 50% a 1R / TP2 30% a 2R / 20% trailing 2xATR)
  B. 100% en trailing (Donchian(20), aucun TP)
Structure C (50% puis trailing du reliquat) NECESSITERAIT une
modification du code de gestion de position meme en isole (le chemin
"tp1 touche, tp2 jamais defini" ne declenche aujourd'hui AUCUN trailing
dans _evaluate_position_management - reste bloque au breakeven) - non
execute, signale explicitement plutot que bricole a la hate.

FENETRE : brulee uniquement (2024-06-14 -> aujourd'hui), PAS 2019-today
comme initialement demande - ce dernier engloberait le holdout pur
2019-2024.06 jamais consulte a ce jour. Substitution documentee dans
docs/DECISIONS.md.

Aucune ecriture DB, aucun appel reseau, aucune modification de
src/executor.py (le remplacement de fonction ci-dessous est un
monkeypatch LOCAL a ce script, jamais applique au code de production).
"""

import json
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.backtest_engine as backtest_engine
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.hypothesis3_strategy import evaluate_entry as h3_evaluate_entry
from src.mean_reversion_strategy import evaluate_entry as h4_evaluate_entry
from src.hypothesis5_strategy import evaluate_entry as h5_evaluate_entry
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import TrendSignal, compute_tp_levels
from src.trend_strategy import evaluate_entry as h1_evaluate_entry

HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]
BURN_START = "2024-06-14T00:00:00"
BURN_END = "2026-08-28T23:59:59"
ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75

# (label, entry_fn, resolution, require_regime_confirmation)
HYPOTHESES = {
    "H1": (h1_evaluate_entry, "HOUR", False),
    "H3": (h3_evaluate_entry, "MINUTE_15", True),
    "H4": (h4_evaluate_entry, "MINUTE_15", True),
    "H5": (h5_evaluate_entry, "MINUTE_15", False),
}

_bar_cache = {}


def load_bars(epic, resolution):
    key = (epic, resolution)
    if key in _bar_cache:
        return _bar_cache[key]
    raw = json.loads((HISTORICAL_DIR / f"{epic}_{resolution}.json").read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None]
    _bar_cache[key] = bars
    return bars


def window(bars, start, end):
    return [b for b in bars if start <= b.time_utc < end]


def zero_spread(bar):
    mo = (bar.open_bid + bar.open_ask) / 2; mh = (bar.high_bid + bar.high_ask) / 2
    ml = (bar.low_bid + bar.low_ask) / 2; mc = (bar.close_bid + bar.close_ask) / 2
    return HistoricalBar(time_utc=bar.time_utc, open_bid=mo, open_ask=mo, high_bid=mh, high_ask=mh, low_bid=ml, low_ask=ml, close_bid=mc, close_ask=mc)


def force_structure(entry_fn, structure):
    """Reduit N'IMPORTE QUEL signal (TrendSignal, MeanReversionSignal...)
    a (asset, direction, entry_price, stop_price) puis reconstruit un
    TrendSignal frais avec tp1/tp2 fixes par la structure testee - le
    declencheur d'entree et le stop initial ne sont JAMAIS modifies."""
    def wrapped(asset, candles):
        signal = entry_fn(asset, candles)
        if signal is None:
            return None
        if structure == "A":
            tp1, tp2 = compute_tp_levels(signal.direction, signal.entry_price, signal.stop_price, 1.0, 2.0)
        else:  # B
            tp1, tp2 = None, None
        return TrendSignal(asset=signal.asset, direction=signal.direction, entry_price=signal.entry_price, stop_price=signal.stop_price, tp1=tp1, tp2=tp2)
    return wrapped


def run_one(hypothesis_label, structure, zero_cost):
    entry_fn_base, resolution, require_regime = HYPOTHESES[hypothesis_label]
    entry_fn = force_structure(entry_fn_base, structure)
    risk_engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL), whitelist=ASSET_WHITELIST)

    all_r = []
    for asset in ALL_ASSETS:
        own_bars = window(load_bars(asset, resolution), BURN_START, BURN_END)
        confirming_bars = None
        if require_regime:
            confirming_bars = {
                "US30": window(load_bars("US30", resolution), BURN_START, BURN_END),
                "US100": window(load_bars("US100", resolution), BURN_START, BURN_END),
            }
        if zero_cost:
            own_bars = [zero_spread(b) for b in own_bars]
            if confirming_bars:
                confirming_bars = {k: [zero_spread(b) for b in v] for k, v in confirming_bars.items()}
            old_fin = backtest_engine.FINANCING_BPS_PER_DAY
            backtest_engine.FINANCING_BPS_PER_DAY = 0.0
            try:
                result = replay_hypothesis(
                    asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
                    require_regime_confirmation=require_regime, confirming_bars=confirming_bars,
                    is_donchian_trailing=(structure == "B"), slippage_multiplier=0.0,
                )
            finally:
                backtest_engine.FINANCING_BPS_PER_DAY = old_fin
        else:
            result = replay_hypothesis(
                asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
                require_regime_confirmation=require_regime, confirming_bars=confirming_bars,
                is_donchian_trailing=(structure == "B"),
            )
        all_r.extend(t.r_multiple_total for t in result.trades)

    if len(all_r) < 2:
        return {"n": len(all_r), "mean": None, "sigma": None, "skew": None, "p90": None}
    mean = statistics.fmean(all_r)
    sigma = statistics.stdev(all_r)
    # skewness (Fisher-Pearson, non corrige de biais - suffisant pour un signe/ordre de grandeur)
    if sigma > 0:
        skew = sum(((r - mean) / sigma) ** 3 for r in all_r) / len(all_r)
    else:
        skew = 0.0
    sorted_r = sorted(all_r)
    p90_idx = min(len(sorted_r) - 1, int(0.9 * len(sorted_r)))
    p90 = sorted_r[p90_idx]
    return {"n": len(all_r), "mean": mean, "sigma": sigma, "skew": skew, "p90": p90}


def main():
    print(f"Fenetre BRULEE utilisee : {BURN_START} -> {BURN_END} (PAS 2019-today, voir docs/DECISIONS.md)\n")
    print("| Hypothese | Structure | n | brut | net | sigma | skew | p90 |")
    print("|---|---|---|---|---|---|---|---|")
    results = {}
    for hyp in HYPOTHESES:
        for structure in ("A", "B"):
            gross = run_one(hyp, structure, zero_cost=True)
            net = run_one(hyp, structure, zero_cost=False)
            results[(hyp, structure)] = {"gross": gross, "net": net}
            g = gross["mean"]; ne = net["mean"]
            print(f"| {hyp} | {structure} | {net['n']} | {g:.4f}R | {ne:.4f}R | {net['sigma']:.4f} | {net['skew']:.4f} | {net['p90']:.4f}R |" if g is not None and ne is not None else f"| {hyp} | {structure} | {net['n']} | N/A | N/A | N/A | N/A | N/A |")

    print("\n=== Ecarts B - A (net), et brut ===")
    for hyp in HYPOTHESES:
        a_net = results[(hyp, "A")]["net"]["mean"]
        b_net = results[(hyp, "B")]["net"]["mean"]
        a_gross = results[(hyp, "A")]["gross"]["mean"]
        b_gross = results[(hyp, "B")]["gross"]["mean"]
        if a_net is not None and b_net is not None:
            print(f"{hyp}: net B-A = {b_net - a_net:+.4f}R | brut B-A = {b_gross - a_gross:+.4f}R")
        else:
            print(f"{hyp}: donnees insuffisantes")


if __name__ == "__main__":
    main()
