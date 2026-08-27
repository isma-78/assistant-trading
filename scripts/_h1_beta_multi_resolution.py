"""
_h1_beta_multi_resolution.py — etape 4c (chaine complete, 27/08/2026) :
mesure l'espérance BRUTE (cout nul) de l'Hypothese #1 (Donchian(20)+
MA200, trend_strategy.evaluate_entry, INCHANGEE) sur la FENETRE BRULEE
uniquement (2024-06-14 -> aujourd'hui, jamais le holdout 2019-2024.06),
a plusieurs resolutions (M15, HOUR, HOUR_4), pour ajuster
beta = pente de ln(brut) vs ln(T) — parametre de nuisance sur donnees
deja vues, meme statut que sigma. Aucune ecriture DB, aucun appel reseau.
"""

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.backtest_engine as backtest_engine
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import evaluate_entry as h1_evaluate_entry

HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
ASSETS = ["USDJPY", "GBPUSD", "EURUSD"]
BURN_START = "2024-06-14T00:00:00"
BURN_END = "2026-08-27T23:59:59"
ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75

RESOLUTIONS = {"MINUTE_15": 15, "HOUR": 60, "HOUR_4": 240}


def load_bars(epic, resolution):
    path = HISTORICAL_DIR / f"{epic}_{resolution}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw) if b is not None]


def window(bars, start, end):
    return [b for b in bars if start <= b.time_utc < end]


def zero_spread(bar):
    mo = (bar.open_bid + bar.open_ask) / 2
    mh = (bar.high_bid + bar.high_ask) / 2
    ml = (bar.low_bid + bar.low_ask) / 2
    mc = (bar.close_bid + bar.close_ask) / 2
    return HistoricalBar(time_utc=bar.time_utc, open_bid=mo, open_ask=mo, high_bid=mh, high_ask=mh, low_bid=ml, low_ask=ml, close_bid=mc, close_ask=mc)


def measure(resolution):
    risk_engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL), whitelist=ASSET_WHITELIST)
    all_gross = []
    per_asset_n = {}
    for asset in ASSETS:
        bars = load_bars(asset, resolution)
        if bars is None:
            return None
        bars = window(bars, BURN_START, BURN_END)
        zbars = [zero_spread(b) for b in bars]
        old_fin = backtest_engine.FINANCING_BPS_PER_DAY
        backtest_engine.FINANCING_BPS_PER_DAY = 0.0
        try:
            result = replay_hypothesis(
                asset, zbars, h1_evaluate_entry, risk_engine, ASSET_WHITELIST,
                ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=False,
                confirming_bars=None, is_donchian_trailing=True, slippage_multiplier=0.0,
            )
        finally:
            backtest_engine.FINANCING_BPS_PER_DAY = old_fin
        r = [t.r_multiple_total for t in result.trades]
        all_gross.extend(r)
        per_asset_n[asset] = len(r)
    if not all_gross:
        return {"n": 0, "mean_gross": None, "per_asset_n": per_asset_n}
    return {"n": len(all_gross), "mean_gross": statistics.fmean(all_gross), "per_asset_n": per_asset_n}


def main():
    results = {}
    for res, T in RESOLUTIONS.items():
        r = measure(res)
        results[res] = r
        print(f"{res} (T={T}): {r}")

    points = [(RESOLUTIONS[res], r["mean_gross"]) for res, r in results.items() if r and r["mean_gross"] is not None and r["mean_gross"] > 0]
    print("\nPoints exploitables (T, brut>0) :", points)
    if len(points) >= 2:
        points.sort()
        (t1, b1), (t2, b2) = points[0], points[-1]
        beta = math.log(b2 / b1) / math.log(t2 / t1)
        print(f"beta (entre T={t1} et T={t2}) = {beta:.4f}")
    else:
        print("Pas assez de points positifs pour ajuster beta.")


if __name__ == "__main__":
    main()
