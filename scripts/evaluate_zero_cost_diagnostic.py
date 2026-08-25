"""
evaluate_zero_cost_diagnostic.py — Phase 1 du chantier de refonte H2-H5
(docs/HYPOTHESES.md, 25/08/2026, "diagnostic coût/edge"). PAS une
recherche de paramètres : aucune variable ajustée, aucun candidat
sélectionné, pas de découpage entraînement/validation — une SEULE mesure
par couple (actif, hypothèse), configuration ACTUELLEMENT déployée,
2 ans, tous actifs.

Compare l'espérance nette actuelle (modèle de coûts §2.6 inchangé) à
l'espérance BRUTE (coûts forcés à zéro) sur EXACTEMENT les mêmes bougies
et la même logique d'entrée — seule la simulation d'exécution change.
« Coûts à zéro » = spread nul (bougies synthétiques bid=ask=mid,
construites ici, jamais une modification de backtest_engine.py) +
slippage_multiplier=0 (déjà paramétrable) + financement à zéro
(FINANCING_BPS_PER_DAY monkeypatché à 0.0 le temps du rejeu brut,
restauré ensuite — constante lue au moment de l'appel, pas un défaut de
fonction, patch sûr, même technique que
scripts/evaluate_hypothesis_candidates.py).

Aucune écriture DB, aucun appel réseau — lecture seule sur
data/historical/ déjà téléchargé.

Usage :
    python scripts/evaluate_zero_cost_diagnostic.py
"""

import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.backtest_engine as backtest_engine
import src.hypothesis2_strategy as h2_mod
import src.hypothesis3_strategy as h3_mod
import src.hypothesis5_strategy as h5_mod
import src.mean_reversion_strategy as h4_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

HYPOTHESES = {
    "H2": {"module": h2_mod, "require_regime": False, "is_donchian": False},
    "H3": {"module": h3_mod, "require_regime": True, "is_donchian": False},
    "H4": {"module": h4_mod, "require_regime": True, "is_donchian": False},
    "H5": {"module": h5_mod, "require_regime": False, "is_donchian": False},
}

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str) -> List[HistoricalBar]:
    if epic in _bar_cache:
        return _bar_cache[epic]
    path = HISTORICAL_DIR / f"{epic}_MINUTE_15.json"
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


def _run_one(hyp_key: str, asset: str, zero_cost: bool) -> tuple:
    cfg = HYPOTHESES[hyp_key]
    load_fn = _load_zero_spread_bars if zero_cost else _load_bars
    own_bars = load_fn(asset)
    confirming_bars = None
    if cfg["require_regime"]:
        confirming_bars = {"US30": load_fn("US30"), "US100": load_fn("US100")}
    risk_engine = _make_risk_engine()
    result = replay_hypothesis(
        asset, own_bars, cfg["module"].evaluate_entry, risk_engine, ASSET_WHITELIST,
        ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=cfg["require_regime"],
        confirming_bars=confirming_bars, is_donchian_trailing=cfg["is_donchian"],
        slippage_multiplier=0.0 if zero_cost else backtest_engine.SLIPPAGE_SPREAD_MULTIPLIER,
    )
    return result.trades


def main() -> None:
    print("Phase 1 — Diagnostic coût/edge. Configuration actuellement déployée, 2 ans, tous actifs.")
    print("Aucune variable ajustée, aucun candidat sélectionné.\n")

    # Spread moyen réel par actif (fait de marché, indépendant de l'hypothèse)
    print("=== Spread moyen réel constaté par actif (spread_open, unité de prix) ===")
    avg_spread_by_asset = {}
    for asset in ALL_ASSETS:
        bars = _load_bars(asset)
        avg_spread = statistics.fmean(b.spread_open for b in bars)
        avg_spread_by_asset[asset] = avg_spread
        print(f"  {asset}: {avg_spread:.6f}")
    print()

    print("| Hyp. | Actif | n (net) | Espérance NETTE | n (brut) | Espérance BRUTE | Coût en R (brut-net) | Stop moyen (prix) |")
    print("|---|---|---|---|---|---|---|---|")

    summary_rows = []
    for hyp_key, cfg in HYPOTHESES.items():
        for asset in ALL_ASSETS:
            trades_net = _run_one(hyp_key, asset, zero_cost=False)
            r_net = [t.r_multiple_total for t in trades_net]
            n_net = len(r_net)
            mean_net = statistics.fmean(r_net) if r_net else None

            old_financing = backtest_engine.FINANCING_BPS_PER_DAY
            backtest_engine.FINANCING_BPS_PER_DAY = 0.0
            try:
                trades_gross = _run_one(hyp_key, asset, zero_cost=True)
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
            print(f"| {hyp_key} | {asset} | {n_net} | {mean_net_str} | {n_gross} | {mean_gross_str} | {cost_r_str} | {avg_stop_str} |")

            summary_rows.append({
                "hyp": hyp_key, "asset": asset, "n_net": n_net, "mean_net": mean_net,
                "n_gross": n_gross, "mean_gross": mean_gross, "cost_r": cost_r, "avg_stop": avg_stop,
                "avg_spread": avg_spread_by_asset[asset],
            })

    print("\n=== Classement coût/R (du plus au moins pénalisé, couples avec n_net>=10) ===")
    ranked = [r for r in summary_rows if r["cost_r"] is not None and r["n_net"] >= 10]
    ranked.sort(key=lambda r: r["cost_r"], reverse=True)
    for r in ranked:
        ratio = (r["cost_r"] / abs(r["avg_stop"])) if r["avg_stop"] else None
        print(f"  {r['hyp']}/{r['asset']}: coût={r['cost_r']:.4f}R spread_moyen={r['avg_spread']:.6f} stop_moyen={r['avg_stop']:.6f}")

    print("\n=== Bilan par hypothèse (moyenne pondérée des couples, n_net>=10) ===")
    for hyp_key in HYPOTHESES:
        rows = [r for r in summary_rows if r["hyp"] == hyp_key and r["mean_gross"] is not None and r["n_net"] >= 10]
        if not rows:
            print(f"  {hyp_key}: aucune donnée exploitable")
            continue
        total_n = sum(r["n_net"] for r in rows)
        weighted_gross = sum(r["mean_gross"] * r["n_gross"] for r in rows) / sum(r["n_gross"] for r in rows)
        weighted_net = sum(r["mean_net"] * r["n_net"] for r in rows) / total_n
        print(f"  {hyp_key}: espérance nette pondérée={weighted_net:.4f}R, espérance BRUTE pondérée={weighted_gross:.4f}R, n total={total_n}")

    print("\nTerminé.")


if __name__ == "__main__":
    main()
