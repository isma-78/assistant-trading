import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import evaluate_entry as h1_evaluate_entry

HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
ASSETS = ["USDJPY", "GBPUSD", "EURUSD"]  # US30 exclu (Branche B, pas d'edge brut)

BURN_START = "2024-06-14T00:00:00"
BURN_END = "2026-08-26T23:59:59"  # "aujourd'hui" - large, filtre par le contenu reel du fichier

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75


def load_bars(epic):
    raw = json.loads((HISTORICAL_DIR / f"{epic}_HOUR.json").read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw) if b is not None]


def window(bars, start, end):
    return [b for b in bars if start <= b.time_utc < end]


def main():
    risk_engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL), whitelist=ASSET_WHITELIST)
    all_r = []
    per_asset = {}
    for asset in ASSETS:
        bars = window(load_bars(asset), BURN_START, BURN_END)
        result = replay_hypothesis(
            asset, bars, h1_evaluate_entry, risk_engine, ASSET_WHITELIST,
            ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=False,
            confirming_bars=None, is_donchian_trailing=True,
        )
        r = [t.r_multiple_total for t in result.trades]
        all_r.extend(r)
        per_asset[asset] = (len(r), statistics.fmean(r) if r else None, min((b.time_utc for b in bars), default=None), max((b.time_utc for b in bars), default=None))

    n = len(all_r)
    mean = statistics.fmean(all_r)
    stdev = statistics.stdev(all_r)
    print(f"BURNED WINDOW (corrected cost model) H1/HOUR, USDJPY+GBPUSD+EURUSD pooled")
    print(f"n={n} mean(NET, corrected)={mean:.4f}R stdev(sample)={stdev:.4f}")
    for a, v in per_asset.items():
        print(" ", a, v)


if __name__ == "__main__":
    main()
