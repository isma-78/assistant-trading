import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.confidence_scorer as cs

ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]
BACKTEST_SOURCES = {
    "H1": "hypothesis_backtest",
    "H2": "hypothesis2_backtest",
    "H3": "hypothesis3_backtest",
    "H4": "hypothesis4_backtest",
    "H5": "hypothesis5_backtest",
}
RISK_PERCENT_DEFAULT = 2.0


def snapshot(db_path):
    rows = {}
    for hyp, source in BACKTEST_SOURCES.items():
        for asset in ALL_ASSETS:
            score = cs.compute_confidence_score(
                db_path, asset, source, RISK_PERCENT_DEFAULT,
                phase_a_min_trades=cs.PHASE_A_MIN_TRADES_BACKTEST,
                phase_b_min_trades=cs.PHASE_B_MIN_TRADES_BACKTEST,
            )
            enough, _, phase = cs.check_min_trades(
                score.nb_trades, cs.PHASE_A_MIN_TRADES_BACKTEST, cs.PHASE_B_MIN_TRADES_BACKTEST,
            )
            blocked = bool(enough and score.esperance_r is not None and score.esperance_r <= 0)
            rows[(hyp, asset)] = (score.nb_trades, score.esperance_r, enough, blocked)
    return rows


def main():
    if len(sys.argv) != 2:
        print("usage: _option_b_status_snapshot.py <db_path>")
        sys.exit(1)
    rows = snapshot(sys.argv[1])
    for (hyp, asset), (n, esp, enough, blocked) in rows.items():
        esp_str = f"{esp:.4f}R" if esp is not None else "N/A"
        print(f"{hyp}\t{asset}\tn={n}\tesperance={esp_str}\teligible={enough}\tBLOQUE={blocked}")


if __name__ == "__main__":
    main()
