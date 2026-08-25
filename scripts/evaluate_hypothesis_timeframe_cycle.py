"""
evaluate_hypothesis_timeframe_cycle.py — Outil de recherche PONCTUEL
(même statut que calibrate_pip_value.py / evaluate_hypothesis_candidates.py),
cycle 2 de l'évolution H3/H4/H5, pré-enregistré dans docs/HYPOTHESES.md
(25/08/2026, section "cycle 2" — candidats, correction statistique et
critères y sont figés AVANT ce script). H1 et H2 hors périmètre ce
cycle (H2 reportée au cycle 3, volume insuffisant).

Ne fait AUCUN appel réseau et n'écrit dans AUCUNE base de données —
lecture seule sur les fichiers JSON déjà téléchargés (data/historical/),
calcul en mémoire, impression du rapport sur stdout. L'application d'un
candidat gagnant (override de paramètre ou changement de résolution) est
faite SÉPARÉMENT par src/hypothesis_evolution_cycle.py, jamais ici.

Méthode (voir docs/HYPOTHESES.md pour le détail complet) :
1. Pour chaque hypothèse (H3/H4/H5), pour chaque candidat (résolution
   d'entrée +/- résolution de confirmation) : rejoue sur l'ENTRAÎNEMENT
   seul (< CUTOFF), 8 actifs poolés.
2. Qualification : n >= PHASE_B_MIN_TRADES_BACKTEST ET (moyenne - z*SE) > 0,
   où z est le quantile normal corrigé (Bonferroni, m = nb candidats de
   l'hypothèse). Corrigé, pas un simple seuil ponctuel (cycle 2, plus de
   candidats testés qu'au cycle 1).
3. Le qualifiant à l'espérance la plus élevée est rejoué UNE FOIS sur la
   VALIDATION seule. PASS si espérance > 0 ET n >= PHASE_A_MIN_TRADES_BACKTEST.

Usage :
    python scripts/evaluate_hypothesis_timeframe_cycle.py
"""

import json
import math
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis3_strategy as h3_mod
import src.hypothesis5_strategy as h5_mod
import src.mean_reversion_strategy as h4_mod
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
    train = [b for b in bars if b.time_utc < cutoff]
    validation = [b for b in bars if b.time_utc >= cutoff]
    return train, validation


@contextmanager
def _override_attrs(module, **kwargs):
    old = {k: getattr(module, k) for k in kwargs}
    for k, v in kwargs.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(module, k, v)


# candidate: (entry_resolution, confirming_resolution_or_None)
HYPOTHESES = {
    "H3": {
        "module": h3_mod, "require_regime": True, "is_donchian": False,
        "candidates": {
            "A": ("MINUTE_15", "MINUTE_15"),
            "B": ("HOUR", "HOUR"),
            "C": ("MINUTE_15", "HOUR"),
        },
    },
    "H4": {
        "module": h4_mod, "require_regime": True, "is_donchian": False,
        "candidates": {
            "A": ("MINUTE_15", "MINUTE_15"),
            "B": ("HOUR", "HOUR"),
            "C": ("MINUTE_15", "HOUR"),
        },
    },
    "H5": {
        "module": h5_mod, "require_regime": False, "is_donchian": False,
        "candidates": {
            "A": ("MINUTE_15", None),
            "B": ("HOUR", None),
        },
    },
}


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _bonferroni_z(m: int) -> float:
    """Quantile normal unilatéral corrigé (Bonferroni, alpha global 0.05).
    Approximation rationnelle d'Abramowitz & Stegun (26.2.23), erreur < 4.5e-4
    — pas de dépendance à scipy, cohérent avec le reste du projet (aucune
    dépendance statistique lourde ajoutée pour ce besoin ponctuel)."""
    alpha = 0.05 / m
    p = 1 - alpha
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    z = t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)
    return z


def _run_period(hyp_key: str, own_period: Dict[str, List[HistoricalBar]], confirming_period: Optional[Dict[str, List[HistoricalBar]]]) -> tuple:
    cfg = HYPOTHESES[hyp_key]
    entry_fn = cfg["module"].evaluate_entry
    risk_engine = _make_risk_engine()
    all_r: List[float] = []
    per_asset = {}
    for asset in ALL_ASSETS:
        own_bars = own_period.get(asset, [])
        if len(own_bars) < 50:
            per_asset[asset] = (0, None)
            continue
        confirming_bars = None
        if cfg["require_regime"] and confirming_period is not None:
            confirming_bars = {"US30": confirming_period.get("US30", []), "US100": confirming_period.get("US100", [])}
        result = replay_hypothesis(
            asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
            require_regime_confirmation=cfg["require_regime"], confirming_bars=confirming_bars, is_donchian_trailing=cfg["is_donchian"],
        )
        r_values = [t.r_multiple_total for t in result.trades]
        all_r.extend(r_values)
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)
    n = len(all_r)
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if len(all_r) >= 2 else None
    return n, mean, stdev, per_asset, all_r


def _load_split(epic: str, resolution: str) -> tuple:
    bars = _load_bars(epic, resolution)
    return _split(bars, CUTOFF)


def main() -> None:
    print(f"Cycle 2 — axe timeframe. Découpage : ENTRAÎNEMENT < {CUTOFF} | VALIDATION >= {CUTOFF}")
    print(f"Correction Bonferroni par famille d'hypothèse (alpha global 0.05).\n")

    for hyp_key, cfg in HYPOTHESES.items():
        m = len(cfg["candidates"])
        z = _bonferroni_z(m)
        print(f"=== {hyp_key} — {m} candidats, z corrigé = {z:.4f} — ENTRAÎNEMENT ===")
        best_candidate, best_mean = None, None
        best_resolutions = None

        for cand_name, (entry_res, confirm_res) in cfg["candidates"].items():
            own_train = {a: _load_split(a, entry_res)[0] for a in ALL_ASSETS}
            confirming_train = None
            if cfg["require_regime"]:
                confirm_res_eff = confirm_res or entry_res
                confirming_train = {
                    "US30": _load_split("US30", confirm_res_eff)[0],
                    "US100": _load_split("US100", confirm_res_eff)[0],
                }
            n, mean, stdev, per_asset, _ = _run_period(hyp_key, own_train, confirming_train)
            if n >= 2 and mean is not None and stdev is not None:
                se = stdev / math.sqrt(n)
                lower_bound = mean - z * se
            else:
                se, lower_bound = None, None
            mean_str = f"{mean:.4f}R" if mean is not None else "N/A"
            lb_str = f"{lower_bound:.4f}R" if lower_bound is not None else "N/A"
            qualifies = n >= PHASE_B_MIN_TRADES_BACKTEST and lower_bound is not None and lower_bound > 0
            label = f"entrée={entry_res} confirm={confirm_res or 'n/a'}"
            print(f"  Candidat {cand_name} ({label}) : n={n} moyenne={mean_str} borne_basse_corrigée={lb_str} {'[QUALIFIE]' if qualifies else ''}")
            for asset, (an, ae) in per_asset.items():
                ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
                print(f"      {asset}: n={an} espérance={ae_str}")
            if qualifies and (best_mean is None or mean > best_mean):
                best_candidate, best_mean = cand_name, mean
                best_resolutions = (entry_res, confirm_res)

        if best_candidate is None:
            print(f"  -> Aucun candidat qualifié (après correction) sur l'entraînement pour {hyp_key} — validation NON consultée.\n")
            continue

        entry_res, confirm_res = best_resolutions
        print(f"  -> Candidat retenu : {best_candidate} (entrée={entry_res}, confirm={confirm_res or 'n/a'}, moyenne entraînement {best_mean:.4f}R) — passage en VALIDATION (un seul essai).")

        own_val = {a: _load_split(a, entry_res)[1] for a in ALL_ASSETS}
        confirming_val = None
        if cfg["require_regime"]:
            confirm_res_eff = confirm_res or entry_res
            confirming_val = {"US30": _load_split("US30", confirm_res_eff)[1], "US100": _load_split("US100", confirm_res_eff)[1]}
        n_val, mean_val, _, per_asset_val, _ = _run_period(hyp_key, own_val, confirming_val)
        mean_val_str = f"{mean_val:.4f}R" if mean_val is not None else "N/A"
        passed = mean_val is not None and mean_val > 0 and n_val >= PHASE_A_MIN_TRADES_BACKTEST
        print(f"  === VALIDATION {hyp_key} candidat {best_candidate} : n={n_val} espérance={mean_val_str} -> {'PASS' if passed else 'FAIL'} ===")
        for asset, (an, ae) in per_asset_val.items():
            ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
            print(f"      {asset}: n={an} espérance={ae_str}")
        print()

    print("Terminé.")


if __name__ == "__main__":
    main()
