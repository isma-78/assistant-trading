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

BURN_START = "2024-06-14T00:00:00"
BURN_END = "2025-12-01T00:00:00"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75


def load_bars(epic):
    raw = json.loads((HISTORICAL_DIR / f"{epic}_HOUR_4.json").read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw) if b is not None]


def window(bars, start, end):
    return [b for b in bars if start <= b.time_utc < end]


def main():
    risk_engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL), whitelist=ASSET_WHITELIST)
    us30_all = load_bars("US30")
    us100_all = load_bars("US100")
    us30_burn = window(us30_all, BURN_START, BURN_END)
    us100_burn = window(us100_all, BURN_START, BURN_END)

    all_r = []
    per_asset = {}
    for asset in ALL_ASSETS:
        bars = load_bars(asset)
        train = window(bars, BURN_START, BURN_END)
        if len(train) < 50:
            per_asset[asset] = (0, None)
            continue
        result = replay_hypothesis(
            asset, train, h3_mod.evaluate_entry, risk_engine, ASSET_WHITELIST,
            ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=True,
            confirming_bars={"US30": us30_burn, "US100": us100_burn},
        )
        r_values = [t.r_multiple_total for t in result.trades]
        all_r.extend(r_values)
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None, min((b.time_utc for b in train), default=None), max((b.time_utc for b in train), default=None))

    n = len(all_r)
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if n >= 2 else None
    print(f"BURNED WINDOW [{BURN_START} , {BURN_END}) — H3/HOUR_4, 8 actifs (avec BTCUSD)")
    print(f"n={n} mean={mean:.4f}R stdev(sample, ddof=1)={stdev:.4f}")
    for asset, vals in per_asset.items():
        print(" ", asset, vals)


if __name__ == "__main__":
    main()
