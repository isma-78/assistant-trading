import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis3_strategy as h3_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine

HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

TEST_START = "2019-01-01T00:00:00"
TEST_END = "2024-06-13T00:00:00"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75


def load_bars(epic):
    raw = json.loads((HISTORICAL_DIR / f"{epic}_HOUR_4.json").read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw) if b is not None]


def window(bars, start, end):
    return [b for b in bars if start <= b.time_utc < end]


def main():
    risk_engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL), whitelist=ASSET_WHITELIST)
    us30_test = window(load_bars("US30"), TEST_START, TEST_END)
    us100_test = window(load_bars("US100"), TEST_START, TEST_END)

    all_trades = []
    per_asset = {}
    for asset in ALL_ASSETS:
        bars = window(load_bars(asset), TEST_START, TEST_END)
        result = replay_hypothesis(
            asset, bars, h3_mod.evaluate_entry, risk_engine, ASSET_WHITELIST,
            ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=True,
            confirming_bars={"US30": us30_test, "US100": us100_test},
        )
        all_trades.extend(result.trades)
        r_values = [t.r_multiple_total for t in result.trades]
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)

    n = len(all_trades)
    all_r = [t.r_multiple_total for t in all_trades]
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if n >= 2 else None
    se = stdev / (n ** 0.5) if stdev is not None else None

    print(f"=== HOLDOUT PUR [{TEST_START} , {TEST_END}) — H3/HOUR_4, 8 actifs (avec BTCUSD), UN SEUL PASSAGE ===")
    print(f"n={n} mean={mean:.4f}R stdev(sample)={stdev:.4f} SE={se:.4f}")
    print()
    print("Par actif :")
    for asset, (an, ae) in per_asset.items():
        ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
        print(f"  {asset}: n={an} mean={ae_str}")
    print()
    print("Par année :")
    by_year = {}
    for t in all_trades:
        y = t.signal_time_utc[:4]
        by_year.setdefault(y, []).append(t.r_multiple_total)
    for y in sorted(by_year):
        vals = by_year[y]
        m = statistics.fmean(vals)
        print(f"  {y}: n={len(vals)} mean={m:.4f}R")


if __name__ == "__main__":
    main()
