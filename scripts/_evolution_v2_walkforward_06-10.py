"""
_evolution_v2_walkforward_06-10.py — Étape 2 du mandat du 06/10/2026 :
walk-forward des 4 candidates V2 sur 2019-2022, selon
`docs/PROTOCOLE_EVOLUTION_V2_06-10.md` §5 (pré-enregistré AVANT ce
script). Lecture seule de `data/historical/` (déjà présent localement,
copié en lecture seule du VPS le 05/10/2026). Aucune bougie de la fenêtre
scellée 2023-01-01→2024-06-14 n'est chargée (assertion explicite).

Moteur corrigé, coûts réels (§2.6), refus de resserrement + plafond de
cluster modélisés (A8, `src.simulator_fidelity`). Univers déployé de
chaque hypothèse (CHFJPY exclue pour H2-H4, incluse pour H1).

Usage : python scripts/_evolution_v2_walkforward_06-10.py
Sortie : data/snapshots/evolution_v2_walkforward.json (+ impression lisible)
"""

import json
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_v2  # noqa: E402
import src.hypothesis1_strategy_v3cand as h1_cand  # noqa: E402
import src.hypothesis2_strategy_v2 as h2_v2  # noqa: E402
import src.hypothesis2_strategy_v3cand as h2_cand  # noqa: E402
import src.hypothesis3_strategy_v2 as h3_v2  # noqa: E402
import src.hypothesis3_strategy_v3cand as h3_cand  # noqa: E402
import src.hypothesis4_strategy_v2 as h4_v2  # noqa: E402
import src.hypothesis4_strategy_v3cand as h4_cand  # noqa: E402
from src.asset_whitelist import ASSET_WHITELIST  # noqa: E402
from src.backtest_engine import bar_from_raw, replay_hypothesis  # noqa: E402
from src.evolution_v2_test import (  # noqa: E402
    TradeRef,
    decide_walk_forward,
    diffs_with_baseline_time,
    evaluate_fold,
    one_sample_block_bootstrap_lower_bound,
    select_best_grid_value,
)
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import StopRefusalModel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "snapshots"

SEALED_START, SEALED_END = "2023-01-01", "2024-06-15"
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}
ENVELOPE = 500.0

# Univers RÉELLEMENT déployé de chaque hypothèse (CHFJPY exclue pour
# H2-H4, incluse pour H1 — même composition que
# scripts/_evolution_cells_05-10.py, repris pour cohérence).
ASSETS_H1 = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
ASSETS_H234 = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

# brut_min repris LITTÉRALEMENT de docs/EVOLUTIONS_CANDIDATES.md (05/10),
# jamais recalculé (protocole §5).
EXPECTED_EFFECT = {"H1": 0.24, "H2": 0.13, "H3": 0.13, "H4": 0.10}

# Fidélité simulateur (protocole §4) : A8 n'a mesuré l'écart résiduel
# live/backtest en R que pour H5 — AUCUNE hypothèse H1-H4 n'a de mesure,
# donc fidélité "non établie" pour les 4 -> jamais "fiable" ici.
FIDELITY_RELIABLE = {"H1": False, "H2": False, "H3": False, "H4": False}

HYP = {
    "H1": {
        "assets": ASSETS_H1, "extras": [], "donchian": False,
        "v2_entry": h1_v2.evaluate_entry, "cand_module": h1_cand, "cand_entry": h1_cand.evaluate_entry,
        "grid_var": "ADX_RESUMPTION_WINDOW_CANDLES", "grid": h1_cand.ADX_RESUMPTION_WINDOW_CANDLES_GRID,
    },
    "H2": {
        "assets": ASSETS_H234, "extras": ["HOUR_4", "DAY"], "donchian": False,
        "v2_entry": h2_v2.evaluate_entry, "cand_module": h2_cand, "cand_entry": h2_cand.evaluate_entry,
        "grid_var": "TRANSITION_LOOKBACK_CANDLES", "grid": h2_cand.TRANSITION_LOOKBACK_CANDLES_GRID,
    },
    "H3": {
        "assets": ASSETS_H234, "extras": [], "donchian": False,
        "v2_entry": h3_v2.evaluate_entry, "cand_module": h3_cand, "cand_entry": h3_cand.evaluate_entry,
        "grid_var": "VOLATILITY_EXPANSION_RATIO", "grid": h3_cand.VOLATILITY_EXPANSION_RATIO_GRID,
    },
    "H4": {
        "assets": ASSETS_H234, "extras": [], "donchian": False,
        "v2_entry": h4_v2.evaluate_entry, "cand_module": h4_cand, "cand_entry": h4_cand.evaluate_entry,
        "grid_var": "ADX_FILTER_THRESHOLD", "grid": h4_cand.ADX_FILTER_THRESHOLD_GRID,
    },
}

FOLDS = {
    "a": {"train": ("2019-01-01", "2021-01-01"), "test": ("2021-01-01", "2022-01-01")},
    "b": {"train": ("2019-01-01", "2022-01-01"), "test": ("2022-01-01", "2023-01-01")},
}


@contextmanager
def override(module, name, value):
    old = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, old)


_cache = {}


def _load_full(asset, resolution):
    """Charge une fois, borné à [2019-01-01, 2023-01-01) — jamais la
    fenêtre scellée ni au-delà (ce script ne l'utilise jamais), jamais
    avant 2019-01-01 (corruption bid/ask 2017-2018)."""
    key = (asset, resolution)
    if key not in _cache:
        raw = json.loads((HIST / f"{asset}_{resolution}.json").read_text(encoding="utf-8"))
        bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None and "2019-01-01" <= b.time_utc < SEALED_START]
        assert not any(SEALED_START <= b.time_utc < SEALED_END for b in bars), "fenêtre scellée chargée"
        _cache[key] = bars
    return _cache[key]


def _slice(asset, resolution, start, end):
    return [b for b in _load_full(asset, resolution) if start <= b.time_utc < end]


def _replay(entry_fn, extras, donchian, asset, start, end, stop_refusal):
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, ENVELOPE), whitelist=ASSET_WHITELIST)
    kwargs = {"is_donchian_trailing": donchian, "stop_update_filter": StopRefusalModel() if stop_refusal else None}
    own_from = (_dt(start) - timedelta(days=90)).strftime("%Y-%m-%d")
    if extras:
        extra_from = (_dt(start) - timedelta(days=200)).strftime("%Y-%m-%d")
        kwargs.update(
            extra_resolution_bars={r: _slice(asset, r, extra_from, end) for r in extras},
            own_bar_duration_seconds=3600.0, extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in extras},
        )
    own_bars = _slice(asset, "HOUR", own_from, end)
    result = replay_hypothesis(asset, own_bars, entry_fn, engine, ASSET_WHITELIST, ENVELOPE, 0.75, **kwargs)
    return [t for t in result.trades if start <= t.entry_time_utc < end]


def _dt(iso_date):
    from datetime import datetime
    return datetime.strptime(iso_date, "%Y-%m-%d")


def _as_traderefs(trades):
    return [TradeRef(t.asset, t.direction, t.entry_time_utc, t.r_multiple_total) for t in trades]


def train_and_select(hyp_key, cfg, train_start, train_end):
    """§5 : rejoue la candidate pour chaque valeur de grille sur
    l'apprentissage (tous actifs), élimine n<200, retient l'espérance
    brute la plus élevée."""
    results = {}
    for value in cfg["grid"]:
        all_trades = []
        with override(cfg["cand_module"], cfg["grid_var"], value):
            for asset in cfg["assets"]:
                all_trades += _replay(cfg["cand_entry"], cfg["extras"], cfg["donchian"], asset, train_start, train_end, True)
        n = len(all_trades)
        mean_r = (sum(t.r_multiple_total for t in all_trades) / n) if n else None
        results[value] = (n, mean_r)
        print(f"  [train {hyp_key}] {cfg['grid_var']}={value}: n={n} mean_r={mean_r}", flush=True)
    return results, select_best_grid_value(results)


def run_hypothesis(hyp_key, cfg):
    print(f"\n===== {hyp_key} =====", flush=True)
    fold_results, selected_by_fold = {}, {}
    for fold_label, bounds in FOLDS.items():
        train_results, chosen = train_and_select(hyp_key, cfg, *bounds["train"])
        selected_by_fold[fold_label] = (chosen, train_results)
        print(f"  [{hyp_key} fold {fold_label}] variante retenue : {chosen}", flush=True)

        baseline_trades, candidate_trades = [], []
        test_start, test_end = bounds["test"]
        for asset in cfg["assets"]:
            baseline_trades += _replay(cfg["v2_entry"], cfg["extras"], cfg["donchian"], asset, test_start, test_end, True)
            if chosen is not None:
                with override(cfg["cand_module"], cfg["grid_var"], chosen):
                    candidate_trades += _replay(cfg["cand_entry"], cfg["extras"], cfg["donchian"], asset, test_start, test_end, True)
        fold = evaluate_fold(fold_label, _as_traderefs(baseline_trades), _as_traderefs(candidate_trades) if chosen is not None else [])
        timed_diffs, _, _ = diffs_with_baseline_time(_as_traderefs(baseline_trades), _as_traderefs(candidate_trades) if chosen is not None else [])
        fold_results[fold_label] = (fold, timed_diffs)
        print(f"  [{hyp_key} fold {fold_label}] test n={fold.n_test} mean_base={fold.mean_baseline} "
              f"mean_cand={fold.mean_candidate} diff={fold.diff}", flush=True)

    fold_a, timed_a = fold_results["a"]
    fold_b, timed_b = fold_results["b"]
    pooled_timed = timed_a + timed_b
    lower_bound = one_sample_block_bootstrap_lower_bound(pooled_timed, 0.05 / 4) if pooled_timed else None
    verdict = decide_walk_forward(hyp_key, fold_a, fold_b, lower_bound, EXPECTED_EFFECT[hyp_key], FIDELITY_RELIABLE[hyp_key])
    print(f"  [{hyp_key}] VERDICT : {verdict}", flush=True)
    return {
        "selected_by_fold": {k: v[0] for k, v in selected_by_fold.items()},
        "train_results": {k: v[1] for k, v in selected_by_fold.items()},
        "fold_a": fold_a.__dict__, "fold_b": fold_b.__dict__,
        "lower_bound": lower_bound, "verdict": verdict.__dict__,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    all_results = {}
    for hyp_key, cfg in HYP.items():
        all_results[hyp_key] = run_hypothesis(hyp_key, cfg)
    (OUT / "evolution_v2_walkforward.json").write_text(json.dumps(all_results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
