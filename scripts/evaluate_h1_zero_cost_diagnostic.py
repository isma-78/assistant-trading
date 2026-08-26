"""
evaluate_h1_zero_cost_diagnostic.py — Phase 1 (méthodologie identique à
scripts/evaluate_zero_cost_diagnostic.py, 25/08/2026) appliquée à
l'Hypothèse #1, sur les 4 couples actuellement bloqués en direct par le
garde-fou Option B (docs/DECISIONS.md, 26/08/2026 — investigation des
rejets en direct) : USDJPY, US30, GBPUSD, EURUSD. Demande explicite
d'Ismaël, avant toute décision de refonte ou d'abandon.

Compare l'espérance nette actuelle (modèle de coûts §2.6 inchangé) à
l'espérance BRUTE (coûts forcés à zéro) sur EXACTEMENT les mêmes bougies
et la même logique d'entrée (`trend_strategy.evaluate_entry`, résolution
HOUR, trailing Donchian pur `is_donchian_trailing=True`,
`require_regime_confirmation=False` — H1 n'est jamais appelée avec ce
paramètre, voir docstring de technical_strategy_executor.py) — seule la
simulation d'exécution change. Même technique de coûts nuls que le
script H2-H5 (spread nul via bougies synthétiques bid=ask=mid,
slippage_multiplier=0, financement monkeypatché à 0.0 le temps du rejeu
brut, restauré ensuite).

Aucune écriture DB, aucun appel réseau — lecture seule sur
data/historical/ déjà téléchargé (bougies HOUR déjà présentes pour les
8 actifs, aucun nouveau téléchargement nécessaire).

Usage :
    python scripts/evaluate_h1_zero_cost_diagnostic.py
"""

import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.backtest_engine as backtest_engine
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import evaluate_entry as h1_evaluate_entry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
RESOLUTION = "HOUR"
ASSETS = ["USDJPY", "US30", "GBPUSD", "EURUSD"]

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str) -> List[HistoricalBar]:
    if epic in _bar_cache:
        return _bar_cache[epic]
    path = HISTORICAL_DIR / f"{epic}_{RESOLUTION}.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[epic] = bars
    return bars


def _zero_spread_bar(bar: HistoricalBar) -> HistoricalBar:
    mid_open = (bar.open_bid + bar.open_ask) / 2
    mid_high = (bar.high_bid + bar.high_ask) / 2
    mid_low = (bar.low_bid + bar.low_ask) / 2
    mid_close = (bar.close_bid + bar.close_ask) / 2
    return HistoricalBar(
        time_utc=bar.time_utc,
        open_bid=mid_open, open_ask=mid_open,
        high_bid=mid_high, high_ask=mid_high,
        low_bid=mid_low, low_ask=mid_low,
        close_bid=mid_close, close_ask=mid_close,
    )


_zero_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_zero_spread_bars(epic: str) -> List[HistoricalBar]:
    if epic in _zero_bar_cache:
        return _zero_bar_cache[epic]
    zeroed = [_zero_spread_bar(b) for b in _load_bars(epic)]
    _zero_bar_cache[epic] = zeroed
    return zeroed


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _run_one(asset: str, zero_cost: bool):
    load_fn = _load_zero_spread_bars if zero_cost else _load_bars
    own_bars = load_fn(asset)
    risk_engine = _make_risk_engine()
    result = replay_hypothesis(
        asset, own_bars, h1_evaluate_entry, risk_engine, ASSET_WHITELIST,
        ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=False,
        confirming_bars=None, is_donchian_trailing=True,
        slippage_multiplier=0.0 if zero_cost else backtest_engine.SLIPPAGE_SPREAD_MULTIPLIER,
    )
    return result.trades


def main() -> None:
    print("Phase 1 (H1) — Diagnostic coût/edge sur les 4 couples bloqués par Option B en direct.")
    print("Aucune variable ajustée, aucun candidat sélectionné, aucune écriture DB.\n")

    print("=== Spread moyen réel constaté par actif (spread_open, unité de prix) ===")
    avg_spread_by_asset = {}
    for asset in ASSETS:
        bars = _load_bars(asset)
        avg_spread = statistics.fmean(b.spread_open for b in bars)
        avg_spread_by_asset[asset] = avg_spread
        print(f"  {asset}: {avg_spread:.6f}")
    print()

    print("| Hyp. | Actif | n (net) | Espérance NETTE | n (brut) | Espérance BRUTE | Coût en R (brut-net) | Stop moyen (prix) |")
    print("|---|---|---|---|---|---|---|---|")

    summary_rows = []
    for asset in ASSETS:
        trades_net = _run_one(asset, zero_cost=False)
        r_net = [t.r_multiple_total for t in trades_net]
        n_net = len(r_net)
        mean_net = statistics.fmean(r_net) if r_net else None

        old_financing = backtest_engine.FINANCING_BPS_PER_DAY
        backtest_engine.FINANCING_BPS_PER_DAY = 0.0
        try:
            trades_gross = _run_one(asset, zero_cost=True)
        finally:
            backtest_engine.FINANCING_BPS_PER_DAY = old_financing
        r_gross = [t.r_multiple_total for t in trades_gross]
        n_gross = len(r_gross)
        mean_gross = statistics.fmean(r_gross) if r_gross else None

        stop_distances = [abs(t.entry_price_signal - t.stop_price_signal) for t in trades_net]
        avg_stop = statistics.fmean(stop_distances) if stop_distances else None

        cost_r = (mean_gross - mean_net) if (mean_gross is not None and mean_net is not None) else None

        mean_net_str = f"{mean_net:.4f}R" if mean_net is not None else "N/A"
        mean_gross_str = f"{mean_gross:.4f}R" if mean_gross is not None else "N/A"
        cost_r_str = f"{cost_r:.4f}R" if cost_r is not None else "N/A"
        avg_stop_str = f"{avg_stop:.6f}" if avg_stop is not None else "N/A"
        print(f"| H1 | {asset} | {n_net} | {mean_net_str} | {n_gross} | {mean_gross_str} | {cost_r_str} | {avg_stop_str} |")

        summary_rows.append({
            "asset": asset, "n_net": n_net, "mean_net": mean_net,
            "n_gross": n_gross, "mean_gross": mean_gross, "cost_r": cost_r, "avg_stop": avg_stop,
            "avg_spread": avg_spread_by_asset[asset],
        })

    print("\n=== Classement coût/R (du plus au moins pénalisé) ===")
    ranked = [r for r in summary_rows if r["cost_r"] is not None]
    ranked.sort(key=lambda r: r["cost_r"], reverse=True)
    for r in ranked:
        print(f"  H1/{r['asset']}: coût={r['cost_r']:.4f}R spread_moyen={r['avg_spread']:.6f} stop_moyen={r['avg_stop']:.6f}")

    rows = [r for r in summary_rows if r["mean_gross"] is not None]
    if rows:
        total_n = sum(r["n_net"] for r in rows)
        weighted_gross = sum(r["mean_gross"] * r["n_gross"] for r in rows) / sum(r["n_gross"] for r in rows)
        weighted_net = sum(r["mean_net"] * r["n_net"] for r in rows) / total_n
        print(f"\n=== Bilan pondéré (4 couples) : espérance nette={weighted_net:.4f}R, espérance BRUTE={weighted_gross:.4f}R, n total={total_n} ===")

    print("\nTerminé.")


if __name__ == "__main__":
    main()
