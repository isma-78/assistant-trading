"""
_couples_decouverte_08-10.py — Étape 2 de la Partie 3 (08/10/2026),
docs/PROTOCOLE_COUPLES_08-10.md §3. Matrice des couples (hypothèse,
actif) EN DÉCOUVERTE : espérance nette brute/contractée de la baseline
_v2 ACTUELLEMENT déployée (jamais le combo H2 retiré, jamais une
période antérieure), rejouée sur les deux fenêtres d'espérance du
protocole (sens1 : 2021-2023, sens2 : 2022-2023), coûts réels, refus de
resserrement et plafond de cluster modélisés (même méthodologie que
`docs/PROTOCOLE_EVOLUTION_V2_06-10.md` §5, jamais réécrite).

Lecture seule de `data/historical/`. Aucun appel broker.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_v2  # noqa: E402
import src.hypothesis2_strategy_v2 as h2_v2  # noqa: E402
import src.hypothesis3_strategy_v2 as h3_v2  # noqa: E402
import src.hypothesis4_strategy_v2 as h4_v2  # noqa: E402
import src.hypothesis5_strategy_v2 as h5_v2  # noqa: E402
from src.asset_whitelist import ASSET_WHITELIST  # noqa: E402
from src.backtest_engine import bar_from_raw, replay_hypothesis  # noqa: E402
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import PortfolioTrade, StopRefusalModel, apply_cluster_cap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "snapshots"

ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]  # CHFJPY exclue
ENVELOPE = 500.0
MIN_N_FOR_ELIGIBLE = 100
CONTRACTION_K = 100.0
SEALED_START, SEALED_END = "2023-01-01", "2024-06-15"
MIN_DATE = "2019-01-01"

# Fenêtres du protocole §2 — ESPÉRANCE uniquement ici (Étape 1 a déjà
# calculé les caractéristiques des fenêtres "caractéristique").
EXPECTANCY_WINDOWS = {
    "sens1": ("2021-01-01", "2023-01-01"),
    "sens2": ("2022-01-01", "2023-01-01"),
}

HYP = {
    "H1": {"module": h1_v2, "extras": [], "donchian": False},
    "H2": {"module": h2_v2, "extras": ["HOUR_4", "DAY"], "donchian": False},
    "H3": {"module": h3_v2, "extras": [], "donchian": False},
    "H4": {"module": h4_v2, "extras": [], "donchian": False},
    "H5": {"module": h5_v2, "extras": ["DAY"], "donchian": True},  # aucun tp1/tp2 (docstring du module)
}
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}

_cache = {}


def load_bars(asset, resolution, start, end):
    key = (asset, resolution)
    if key not in _cache:
        raw = json.loads((HIST / f"{asset}_{resolution}.json").read_text(encoding="utf-8"))
        bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None and MIN_DATE <= b.time_utc < SEALED_START]
        assert not any(SEALED_START <= b.time_utc < SEALED_END for b in bars), "fenêtre scellée chargée"
        _cache[key] = bars
    return [b for b in _cache[key] if start <= b.time_utc < end]


def replay_asset(cfg, asset, start, end, stop_refusal):
    from datetime import datetime, timedelta
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, ENVELOPE), whitelist=ASSET_WHITELIST)
    own_from = (datetime.strptime(start[:10], "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    kwargs = {"is_donchian_trailing": cfg["donchian"], "stop_update_filter": StopRefusalModel() if stop_refusal else None}
    if cfg["extras"]:
        extra_from = (datetime.strptime(start[:10], "%Y-%m-%d") - timedelta(days=200)).strftime("%Y-%m-%d")
        kwargs.update(
            extra_resolution_bars={r: load_bars(asset, r, extra_from, end) for r in cfg["extras"]},
            own_bar_duration_seconds=3600.0,
            extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in cfg["extras"]},
        )
    own_bars = load_bars(asset, "HOUR", own_from, end)
    result = replay_hypothesis(asset, own_bars, cfg["module"].evaluate_entry, engine, ASSET_WHITELIST,
                                ENVELOPE, 0.75, **kwargs)
    return [t for t in result.trades if start <= t.entry_time_utc < end]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}

    for window_label, (start, end) in EXPECTANCY_WINDOWS.items():
        print(f"\n===== Fenêtre {window_label} ({start} -> {end}) =====")
        window_results = {}
        all_for_cluster = []
        trades_by_hyp_asset = {}

        for hyp_key, cfg in HYP.items():
            for asset in ASSETS:
                trades = replay_asset(cfg, asset, start, end, stop_refusal=True)
                trades_by_hyp_asset[(hyp_key, asset)] = trades
                all_for_cluster += [
                    PortfolioTrade(hyp_key, asset, t.entry_time_utc, t.exit_time_utc, 10.0, t.r_multiple_total)
                    for t in trades
                ]

        provisional_risk = ENVELOPE * 4.0 / 100.0
        kept, blocked = apply_cluster_cap(all_for_cluster, provisional_risk)
        kept_keys = {(k.source, k.asset, k.entry_time, k.r_multiple) for k in kept}
        print(f"  plafond de cluster : {len(blocked)}/{len(all_for_cluster)} trades backtest bloqués (poolé, {len(HYP)} hypothèses)")

        for hyp_key in HYP:
            window_results[hyp_key] = {}
            for asset in ASSETS:
                trades = trades_by_hyp_asset[(hyp_key, asset)]
                surviving = [t for t in trades if (hyp_key, asset, t.entry_time_utc, t.r_multiple_total) in kept_keys]
                n = len(surviving)
                if n == 0:
                    brut, contracte = None, None
                else:
                    brut = sum(t.r_multiple_total for t in surviving) / n
                    contracte = brut * (n / (n + CONTRACTION_K))
                eligible = n >= MIN_N_FOR_ELIGIBLE
                window_results[hyp_key][asset] = {
                    "n": n, "esperance_brute": brut, "esperance_contractee": contracte, "eligible": eligible,
                }
                flag = "" if eligible else " (n<100, non éligible au rang)"
                print(f"  {hyp_key} {asset}: n={n} brut={brut} contractee={contracte}{flag}")

        results[window_label] = window_results

    (OUT / "couples_decouverte_08-10.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
