"""
evaluate_squeeze_breakout_candidate.py — Outil de recherche PONCTUEL,
pré-enregistré dans docs/HYPOTHESES.md (25/08/2026, "trois chantiers",
chantier 1). Candidat UNIQUE (pas de grille) : breakout de volatilité
(squeeze Bollinger) pour la place de H4 —
`mean_reversion_strategy.evaluate_entry_squeeze_breakout`.

Aucun appel réseau, aucune écriture DB — lecture seule sur les fichiers
JSON déjà téléchargés. Auto-déploiement (si qualifié + validé) fait
SÉPARÉMENT après lecture du rapport produit ici, jamais automatiquement
par ce script — mais voir docs/HYPOTHESES.md : le déploiement lui-même,
une fois ce script exécuté et le résultat connu, ne demande plus de
confirmation manuelle d'Ismaël (écart CDC assumé, chantiers 1/2
uniquement).

Méthode : n >= PHASE_B_MIN_TRADES_BACKTEST (150) ET (moyenne − z×SE) > 0
sur l'entraînement (z pour m=1, un seul candidat : quantile normal
unilatéral à 95%, ≈1.6449). Si qualifié, un seul essai sur la
validation : PASS si n >= PHASE_A_MIN_TRADES_BACKTEST (60) ET espérance
nette > 0.

Usage :
    python scripts/evaluate_squeeze_breakout_candidate.py
"""

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.confidence_scorer import PHASE_A_MIN_TRADES_BACKTEST, PHASE_B_MIN_TRADES_BACKTEST
from src.mean_reversion_strategy import evaluate_entry_squeeze_breakout
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

CUTOFF = "2025-12-01T00:00:00"
ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

# Fenêtre minimale requise par le candidat : BOLLINGER_PERIOD(20) +
# SQUEEZE_LOOKBACK_PERIODS(100) + marge — 220 (DEFAULT_LOOKBACK) suffit
# largement, conservé pour cohérence avec les cycles précédents (pas de
# nouveau paramètre de fenêtre de rejeu introduit ici).


def _load_bars(epic: str) -> List[HistoricalBar]:
    path = HISTORICAL_DIR / f"{epic}_MINUTE_15.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]


def _split(bars: List[HistoricalBar], cutoff: str) -> tuple:
    train = [b for b in bars if b.time_utc < cutoff]
    validation = [b for b in bars if b.time_utc >= cutoff]
    return train, validation


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _one_sided_z_95() -> float:
    """m=1 : quantile normal unilatéral à 95% (Abramowitz & Stegun
    26.2.23), même formule que les cycles précédents avec m=1."""
    alpha = 0.05
    p = 1 - alpha
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)


def _run_period(own_period: Dict[str, List[HistoricalBar]]) -> tuple:
    risk_engine = _make_risk_engine()
    all_r: List[float] = []
    per_asset = {}
    for asset in ALL_ASSETS:
        own_bars = own_period.get(asset, [])
        if len(own_bars) < 250:
            per_asset[asset] = (0, None)
            continue
        result = replay_hypothesis(
            asset, own_bars, evaluate_entry_squeeze_breakout, risk_engine, ASSET_WHITELIST,
            ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=False,
        )
        r_values = [t.r_multiple_total for t in result.trades]
        all_r.extend(r_values)
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)
    n = len(all_r)
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if len(all_r) >= 2 else None
    return n, mean, stdev, per_asset


def main() -> None:
    print(f"Candidat unique — breakout de volatilité (squeeze Bollinger) pour la place de H4.")
    print(f"Découpage : ENTRAÎNEMENT < {CUTOFF} | VALIDATION >= {CUTOFF}\n")

    train_bars: Dict[str, List[HistoricalBar]] = {}
    validation_bars: Dict[str, List[HistoricalBar]] = {}
    for asset in ALL_ASSETS:
        bars = _load_bars(asset)
        train_bars[asset], validation_bars[asset] = _split(bars, CUTOFF)

    z = _one_sided_z_95()
    print(f"z (m=1, unilatéral 95%) = {z:.4f}\n=== ENTRAÎNEMENT ===")
    n, mean, stdev, per_asset = _run_period(train_bars)
    if n >= 2 and mean is not None and stdev is not None:
        se = stdev / math.sqrt(n)
        lower_bound = mean - z * se
    else:
        lower_bound = None
    mean_str = f"{mean:.4f}R" if mean is not None else "N/A"
    lb_str = f"{lower_bound:.4f}R" if lower_bound is not None else "N/A"
    qualifies = n >= PHASE_B_MIN_TRADES_BACKTEST and lower_bound is not None and lower_bound > 0
    print(f"n={n} moyenne={mean_str} borne_basse_corrigée={lb_str} {'[QUALIFIE]' if qualifies else ''}")
    for asset, (an, ae) in per_asset.items():
        ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
        print(f"    {asset}: n={an} espérance={ae_str}")

    if not qualifies:
        print("\n-> Candidat NON qualifié sur l'entraînement — validation NON consultée.")
        print("Terminé.")
        return

    print("\n-> Candidat qualifié — passage en VALIDATION (un seul essai).")
    n_val, mean_val, _, per_asset_val = _run_period(validation_bars)
    mean_val_str = f"{mean_val:.4f}R" if mean_val is not None else "N/A"
    passed = mean_val is not None and mean_val > 0 and n_val >= PHASE_A_MIN_TRADES_BACKTEST
    print(f"=== VALIDATION : n={n_val} espérance={mean_val_str} -> {'PASS' if passed else 'FAIL'} ===")
    for asset, (an, ae) in per_asset_val.items():
        ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
        print(f"    {asset}: n={an} espérance={ae_str}")

    print(f"\nRÉSULTAT FINAL : {'DÉPLOIEMENT AUTOMATIQUE AUTORISÉ' if passed else 'PAS DE DÉPLOIEMENT'}")
    print("Terminé.")


if __name__ == "__main__":
    main()
