import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.backtest_engine as backtest_engine
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


def zero_spread_bar(bar):
    mo = (bar.open_bid + bar.open_ask) / 2
    mh = (bar.high_bid + bar.high_ask) / 2
    ml = (bar.low_bid + bar.low_ask) / 2
    mc = (bar.close_bid + bar.close_ask) / 2
    return HistoricalBar(time_utc=bar.time_utc, open_bid=mo, open_ask=mo, high_bid=mh, high_ask=mh, low_bid=ml, low_ask=ml, close_bid=mc, close_ask=mc)


def window(bars, start, end):
    return [b for b in bars if start <= b.time_utc < end]


def main():
    risk_engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL), whitelist=ASSET_WHITELIST)
    us30_test = [zero_spread_bar(b) for b in window(load_bars("US30"), TEST_START, TEST_END)]
    us100_test = [zero_spread_bar(b) for b in window(load_bars("US100"), TEST_START, TEST_END)]

    old_financing = backtest_engine.FINANCING_BPS_PER_DAY
    backtest_engine.FINANCING_BPS_PER_DAY = 0.0
    all_trades = []
    per_asset = {}
    try:
        for asset in ALL_ASSETS:
            bars = [zero_spread_bar(b) for b in window(load_bars(asset), TEST_START, TEST_END)]
            result = replay_hypothesis(
                asset, bars, h3_mod.evaluate_entry, risk_engine, ASSET_WHITELIST,
                ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=True,
                confirming_bars={"US30": us30_test, "US100": us100_test},
                slippage_multiplier=0.0,
            )
            all_trades.extend(result.trades)
            r_values = [t.r_multiple_total for t in result.trades]
            per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)
    finally:
        backtest_engine.FINANCING_BPS_PER_DAY = old_financing

    n = len(all_trades)
    all_r = [t.r_multiple_total for t in all_trades]
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if n >= 2 else None
    print(f"=== FORENSIQUE (coûts nuls) — H3/HOUR_4, meme fenetre holdout [{TEST_START},{TEST_END}) ===")
    print(f"n={n} mean_BRUT={mean:.4f}R stdev={stdev:.4f}")
    for asset, (an, ae) in per_asset.items():
        print(" ", asset, "n=", an, "mean=", f"{ae:.4f}R" if ae is not None else "N/A")


if __name__ == "__main__":
    main()
