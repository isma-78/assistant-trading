"""
evaluate_refonte_step1_resolution.py — Phase 2, étape 1 (priorité #1) du
chantier de refonte H2-H5 (docs/HYPOTHESES.md, 25/08/2026). Branche A
uniquement (H3, H5 — edge brut positif en Phase 1). Teste la résolution
HOUR et HOUR_4 (bougies 4h) SANS changer le signal d'entrée — même
`evaluate_entry` déployé, seule la résolution des bougies change.

Modèle de coût §2.6 INCHANGÉ (nette, slippage_multiplier=1.0) — ce n'est
plus le diagnostic à coût nul de la Phase 1, c'est une évaluation réelle
en vue d'un déploiement éventuel.

Critère : n >= PHASE_B_MIN_TRADES_BACKTEST (150) ET (moyenne − z×SE) > 0
sur l'ENTRAÎNEMENT seul, correction Bonferroni m=3 (M15 actuel/HOUR/
HOUR_4). Le résultat de cette étape (résolution retenue, éventuellement
M15 par défaut si aucune amélioration) alimente l'étape 2 (stop ATR) —
AUCUNE consultation de la validation ici, réservée au candidat FINAL
(après les 3 étapes), conformément au pré-enregistrement.

Aucune écriture DB, aucun appel réseau — lecture seule sur
data/historical/ (déjà téléchargé, y compris HOUR_4).

Usage :
    python scripts/evaluate_refonte_step1_resolution.py
"""

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis3_strategy as h3_mod
import src.hypothesis5_strategy as h5_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.confidence_scorer import PHASE_A_MIN_TRADES_BACKTEST, PHASE_B_MIN_TRADES_BACKTEST
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

CUTOFF = "2025-12-01T00:00:00"
ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

RESOLUTIONS = ["MINUTE_15", "HOUR", "HOUR_4"]

HYPOTHESES = {
    "H3": {"module": h3_mod, "require_regime": True},
    "H5": {"module": h5_mod, "require_regime": False},
}

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str, resolution: str) -> List[HistoricalBar]:
    key = f"{epic}_{resolution}"
    if key in _bar_cache:
        return _bar_cache[key]
    path = HISTORICAL_DIR / f"{key}.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[key] = bars
    return bars


def _split(bars: List[HistoricalBar], cutoff: str) -> tuple:
    return [b for b in bars if b.time_utc < cutoff], [b for b in bars if b.time_utc >= cutoff]


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _bonferroni_z(m: int) -> float:
    alpha = 0.05 / m
    p = 1 - alpha
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)


def _run_train(hyp_key: str, resolution: str) -> tuple:
    cfg = HYPOTHESES[hyp_key]
    risk_engine = _make_risk_engine()
    all_r: List[float] = []
    per_asset = {}
    for asset in ALL_ASSETS:
        bars = _load_bars(asset, resolution)
        train, _ = _split(bars, CUTOFF)
        if len(train) < 250:
            per_asset[asset] = (0, None)
            continue
        confirming_bars = None
        if cfg["require_regime"]:
            us30_train, _ = _split(_load_bars("US30", resolution), CUTOFF)
            us100_train, _ = _split(_load_bars("US100", resolution), CUTOFF)
            confirming_bars = {"US30": us30_train, "US100": us100_train}
        result = replay_hypothesis(
            asset, train, cfg["module"].evaluate_entry, risk_engine, ASSET_WHITELIST,
            ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=cfg["require_regime"],
            confirming_bars=confirming_bars,
        )
        r_values = [t.r_multiple_total for t in result.trades]
        all_r.extend(r_values)
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)
    n = len(all_r)
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if len(all_r) >= 2 else None
    return n, mean, stdev, per_asset


def main() -> None:
    print("Phase 2, étape 1 — résolution (H3, H5 — branche A). Modèle de coût §2.6 inchangé (nette).")
    z = _bonferroni_z(len(RESOLUTIONS))
    print(f"z (m={len(RESOLUTIONS)}) = {z:.4f}\n")

    for hyp_key in HYPOTHESES:
        print(f"=== {hyp_key} ===")
        best_res, best_mean = None, None
        for resolution in RESOLUTIONS:
            n, mean, stdev, per_asset = _run_train(hyp_key, resolution)
            if n >= 2 and mean is not None and stdev is not None:
                se = stdev / math.sqrt(n)
                lower_bound = mean - z * se
            else:
                lower_bound = None
            mean_str = f"{mean:.4f}R" if mean is not None else "N/A"
            lb_str = f"{lower_bound:.4f}R" if lower_bound is not None else "N/A"
            qualifies = n >= PHASE_B_MIN_TRADES_BACKTEST and lower_bound is not None and lower_bound > 0
            print(f"  {resolution}: n={n} moyenne={mean_str} borne_basse={lb_str} {'[QUALIFIE]' if qualifies else ''}")
            for asset, (an, ae) in per_asset.items():
                ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
                print(f"      {asset}: n={an} espérance={ae_str}")
            if qualifies and (best_mean is None or mean > best_mean):
                best_res, best_mean = resolution, mean
        if best_res is None:
            print(f"  -> Aucune résolution ne qualifie pour {hyp_key} — conservation de MINUTE_15 (défaut), passage à l'étape 2 avec la résolution actuelle.\n")
        else:
            print(f"  -> Résolution retenue pour {hyp_key} : {best_res} (moyenne {best_mean:.4f}R)\n")

    print("Terminé.")


if __name__ == "__main__":
    main()
