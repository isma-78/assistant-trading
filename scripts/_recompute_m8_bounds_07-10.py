"""
_recompute_m8_bounds_07-10.py — Étape 3 du mandat du 07/10/2026 : règle
4 du protocole. **Le simulateur n'a pas été modifié** (voir
docs/FIDELITE_07-10.md §2 — la cause dominante, structure de sortie
TP-fixe-involontaire, reflète un bug déjà corrigé depuis le 25/09 ; la
modéliser reviendrait à calibrer le simulateur sur une configuration
obsolète, contre-productif pour le forward ; le refus de resserrement,
seule autre cause ≥20%, est déjà modélisé depuis le 05/10/A8, donc déjà
présent dans le walk-forward du 06/10). Les résultats du 06/10 restent
donc SEULS valides — ce script recalcule UNIQUEMENT leur borne basse à
m=8 (au lieu de m=4) pour rester comparable, en rejouant la période de
TEST SEULEMENT avec la variante DÉJÀ retenue par l'apprentissage du
06/10 (aucune nouvelle sélection de grille, aucun nouveau choix).

Lecture seule de data/historical/ (2019-2022, fenêtre scellée jamais
chargée). Aucun appel broker.
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
)
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import StopRefusalModel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "snapshots"
START, END = "2019-01-01", "2023-01-01"
ASSETS_H1 = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
ASSETS_H234 = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}
EXPECTED_EFFECT = {"H1": 0.24, "H2": 0.13, "H3": 0.13, "H4": 0.10}
M_NEW = 8

HYP = {
    "H1": {"assets": ASSETS_H1, "extras": [], "donchian": False, "v2_entry": h1_v2.evaluate_entry,
           "cand_module": h1_cand, "cand_entry": h1_cand.evaluate_entry, "grid_var": "ADX_RESUMPTION_WINDOW_CANDLES"},
    "H2": {"assets": ASSETS_H234, "extras": ["HOUR_4", "DAY"], "donchian": False, "v2_entry": h2_v2.evaluate_entry,
           "cand_module": h2_cand, "cand_entry": h2_cand.evaluate_entry, "grid_var": "TRANSITION_LOOKBACK_CANDLES"},
    "H3": {"assets": ASSETS_H234, "extras": [], "donchian": False, "v2_entry": h3_v2.evaluate_entry,
           "cand_module": h3_cand, "cand_entry": h3_cand.evaluate_entry, "grid_var": "VOLATILITY_EXPANSION_RATIO"},
    "H4": {"assets": ASSETS_H234, "extras": [], "donchian": False, "v2_entry": h4_v2.evaluate_entry,
           "cand_module": h4_cand, "cand_entry": h4_cand.evaluate_entry, "grid_var": "ADX_FILTER_THRESHOLD"},
}
FOLDS = {"a": "2021-01-01,2022-01-01", "b": "2022-01-01,2023-01-01"}


@contextmanager
def override(module, name, value):
    old = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, old)


_cache = {}


def load(asset, res):
    key = (asset, res)
    if key not in _cache:
        raw = json.loads((HIST / f"{asset}_{res}.json").read_text(encoding="utf-8"))
        bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None and START <= b.time_utc < END]
        assert not any("2023-01-01" <= b.time_utc < "2024-06-15" for b in bars)
        _cache[key] = bars
    return _cache[key]


def replay(entry_fn, cfg, asset, start, end):
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, 500.0), whitelist=ASSET_WHITELIST)
    kwargs = {"is_donchian_trailing": cfg["donchian"], "stop_update_filter": StopRefusalModel()}
    if cfg["extras"]:
        kwargs.update(extra_resolution_bars={r: load(asset, r) for r in cfg["extras"]},
                      own_bar_duration_seconds=3600.0, extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in cfg["extras"]})
    result = replay_hypothesis(asset, load(asset, "HOUR"), entry_fn, engine, ASSET_WHITELIST, 500.0, 0.75, **kwargs)
    return [t for t in result.trades if start <= t.entry_time_utc < end]


def main() -> int:
    old_results = json.loads((OUT / "evolution_v2_walkforward.json").read_text(encoding="utf-8"))
    new_results = {}
    for key, cfg in HYP.items():
        selected = old_results[key]["selected_by_fold"]
        print(f"\n===== {key} (variantes déjà retenues le 06/10 : {selected}, aucune nouvelle sélection) =====")
        timed_all = []
        folds = {}
        for fold_label, bounds in FOLDS.items():
            test_start, test_end = bounds.split(",")
            value = selected[fold_label]
            baseline, candidate = [], []
            for asset in cfg["assets"]:
                baseline += replay(cfg["v2_entry"], cfg, asset, test_start, test_end)
                with override(cfg["cand_module"], cfg["grid_var"], value):
                    candidate += replay(cfg["cand_entry"], cfg, asset, test_start, test_end)
            baseline_refs = [TradeRef(t.asset, t.direction, t.entry_time_utc, t.r_multiple_total) for t in baseline]
            candidate_refs = [TradeRef(t.asset, t.direction, t.entry_time_utc, t.r_multiple_total) for t in candidate]
            fold = evaluate_fold(fold_label, baseline_refs, candidate_refs)
            timed, _, _ = diffs_with_baseline_time(baseline_refs, candidate_refs)
            timed_all += timed
            folds[fold_label] = fold
            print(f"  fold {fold_label} (variante {value}) : n={fold.n_test} diff={fold.diff}")
        lower_m8 = one_sample_block_bootstrap_lower_bound(timed_all, 0.05 / M_NEW)
        verdict_m8 = decide_walk_forward(key, folds["a"], folds["b"], lower_m8, EXPECTED_EFFECT[key], fidelity_reliable=False)
        old_lower_m4 = old_results[key]["verdict"]["lower_bound"]
        print(f"  ANCIEN (06/10, m=4) : lower_bound={old_lower_m4}")
        print(f"  NOUVEAU (m=8, test seul rejoué, mêmes variantes) : lower_bound={lower_m8}")
        print(f"  VERDICT (fidélité non établie pour les 4, voir etape 1) : {verdict_m8}")
        new_results[key] = {"fold_a": folds["a"].__dict__, "fold_b": folds["b"].__dict__,
                            "lower_bound_m8": lower_m8, "lower_bound_m4_old": old_lower_m4, "verdict_m8": verdict_m8.__dict__}
    (OUT / "fidelity_m8_recompute.json").write_text(json.dumps(new_results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
