"""
evaluate_hypothesis_candidates.py — Outil de recherche PONCTUEL (même
statut que calibrate_pip_value.py), pour l'évolution entraînement/
validation de H2/H3/H4/H5, pré-enregistrée dans docs/HYPOTHESES.md
(25/08/2026, à lire en premier — candidats, critères et découpage
temporel y sont figés AVANT ce script). H1 jamais importée, jamais
touchée.

Ne fait AUCUN appel réseau (historique déjà téléchargé, data/historical/)
et n'écrit dans AUCUNE base de données — lecture seule sur les fichiers
JSON locaux, calcul en mémoire, impression du rapport sur stdout. Le
déploiement éventuel d'un candidat gagnant (mise à jour du fichier de
stratégie réel + rafraîchissement du backtest de production) est fait
SÉPARÉMENT, après lecture du rapport produit ici, jamais automatiquement
par ce script.

Méthode (voir docs/HYPOTHESES.md pour le détail complet) :
1. Découpe chaque série M15 en ENTRAÎNEMENT (< CUTOFF) / VALIDATION (>= CUTOFF).
2. Pour chaque hypothèse, pour chaque candidat pré-enregistré : rejoue
   sur l'ENTRAÎNEMENT SEUL, 8 actifs poolés, calcule espérance nette +
   nombre de trades.
3. Retient le candidat avec l'espérance la plus élevée ET strictement
   positive, avec >= PHASE_B_MIN_TRADES_BACKTEST trades poolés sur
   l'entraînement — sinon aucun candidat retenu pour cette hypothèse.
4. Le candidat retenu (un seul, une seule fois) est rejoué sur la
   VALIDATION seule, jamais consultée avant cette étape. PASS si
   espérance > 0 ET trades >= PHASE_A_MIN_TRADES_BACKTEST, sinon FAIL.

Usage :
    python scripts/evaluate_hypothesis_candidates.py
"""

import json
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis2_strategy as h2_mod
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

# Coupure 2/3 entraînement / 1/3 validation, mesurée sur l'historique
# M15 réellement disponible (2024-06-14T11:00 -> 2026-08-24T17:30),
# arrondie pour lisibilité — voir docs/HYPOTHESES.md (25/08/2026).
CUTOFF = "2025-12-01T00:00:00"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str, resolution: str = "MINUTE_15") -> List[HistoricalBar]:
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


@contextmanager
def _override_bollinger_std(std_multiplier: float):
    """BOLLINGER_STD_MULTIPLIER est un paramètre par DÉFAUT de
    compute_bollinger_bands, lié à la définition de la fonction (pas
    relu à chaque appel) — un simple setattr sur la constante du module
    n'a aucun effet sur les appels déjà en cours. `__defaults__` est en
    revanche relu par Python à CHAQUE appel : le patcher directement
    fonctionne, jamais un artefact du def-time."""
    func = h4_mod.compute_bollinger_bands
    old_defaults = func.__defaults__
    period = old_defaults[0]
    func.__defaults__ = (period, std_multiplier)
    try:
        yield
    finally:
        func.__defaults__ = old_defaults


HYPOTHESES = {
    "H2": {
        "module": h2_mod, "resolution": "MINUTE_15", "require_regime": False, "is_donchian": False,
        "candidates": {
            "A": {},
            "B": {"TP1_R_MULTIPLE": 0.5, "TP2_R_MULTIPLE": 1.5},
        },
    },
    "H3": {
        "module": h3_mod, "resolution": "MINUTE_15", "require_regime": True, "is_donchian": False,
        "candidates": {
            "A": {},
            "B": {"TP1_R_MULTIPLE": 0.5, "TP2_R_MULTIPLE": 1.5},
            "C": {"TP1_R_MULTIPLE": 1.5, "TP2_R_MULTIPLE": 3.0},
        },
    },
    "H4": {
        "module": h4_mod, "resolution": "MINUTE_15", "require_regime": True, "is_donchian": False,
        "candidates": {
            "A": {},
            "B": {"_bollinger_std": 2.5},
            "C": {"STOP_WIDTH_MULTIPLIER": 1.5},
        },
    },
    "H5": {
        "module": h5_mod, "resolution": "MINUTE_15", "require_regime": False, "is_donchian": False,
        "candidates": {
            "A": {},
            "B": {"RSI_PERIOD": 9},
            "C": {"TP1_R_MULTIPLE": 0.5, "TP2_R_MULTIPLE": 1.5},
        },
    },
}


@contextmanager
def _apply_candidate(module, overrides: dict):
    bollinger_std = overrides.pop("_bollinger_std", None) if overrides else None
    attr_overrides = {k: v for k, v in overrides.items()} if overrides else {}
    with _override_attrs(module, **attr_overrides):
        if bollinger_std is not None:
            with _override_bollinger_std(bollinger_std):
                yield
        else:
            yield


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _pooled_stats(all_r_multiples: List[float]) -> tuple:
    if not all_r_multiples:
        return 0, None
    return len(all_r_multiples), statistics.fmean(all_r_multiples)


def _run_period(hyp_key: str, period_bars: Dict[str, List[HistoricalBar]], confirming_period: Optional[Dict[str, List[HistoricalBar]]]) -> tuple:
    """Rejoue l'entry_fn ACTUEL du module (déjà patché par l'appelant)
    sur les bougies de la période donnée, pour les 8 actifs, poolé.
    Retourne (nb_trades, esperance_nette_ou_None, detail_par_actif)."""
    cfg = HYPOTHESES[hyp_key]
    entry_fn = cfg["module"].evaluate_entry
    risk_engine = _make_risk_engine()
    all_r: List[float] = []
    per_asset = {}
    for asset in ALL_ASSETS:
        own_bars = period_bars.get(asset, [])
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
    n, esperance = _pooled_stats(all_r)
    return n, esperance, per_asset


def main() -> None:
    print(f"Découpage : ENTRAÎNEMENT < {CUTOFF} | VALIDATION >= {CUTOFF}")
    print(f"Critère de sélection (entraînement) : espérance > 0 et >= {PHASE_B_MIN_TRADES_BACKTEST} trades poolés.")
    print(f"Critère de succès (validation) : espérance > 0 et >= {PHASE_A_MIN_TRADES_BACKTEST} trades poolés. Un seul essai.\n")

    # Bougies + découpage, une fois pour tous les actifs (réutilisées entre candidats/hypothèses)
    train_bars: Dict[str, List[HistoricalBar]] = {}
    validation_bars: Dict[str, List[HistoricalBar]] = {}
    for asset in ALL_ASSETS:
        bars = _load_bars(asset)
        train_bars[asset], validation_bars[asset] = _split(bars, CUTOFF)
    train_confirming = {"US30": train_bars["US30"], "US100": train_bars["US100"]}
    validation_confirming = {"US30": validation_bars["US30"], "US100": validation_bars["US100"]}

    winners = {}

    for hyp_key, cfg in HYPOTHESES.items():
        print(f"=== {hyp_key} — candidats sur ENTRAÎNEMENT ===")
        best_candidate, best_esperance = None, None
        for cand_name, overrides in cfg["candidates"].items():
            with _apply_candidate(cfg["module"], dict(overrides)):
                n, esperance, per_asset = _run_period(hyp_key, train_bars, train_confirming)
            esp_str = f"{esperance:.4f}R" if esperance is not None else "N/A"
            qualifies = esperance is not None and esperance > 0 and n >= PHASE_B_MIN_TRADES_BACKTEST
            print(f"  Candidat {cand_name} {overrides or '(référence)'} : n={n} espérance={esp_str} {'[QUALIFIE]' if qualifies else ''}")
            for asset, (an, ae) in per_asset.items():
                ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
                print(f"      {asset}: n={an} espérance={ae_str}")
            if qualifies and (best_esperance is None or esperance > best_esperance):
                best_candidate, best_esperance = cand_name, esperance

        if best_candidate is None:
            print(f"  -> Aucun candidat qualifié sur l'entraînement pour {hyp_key} — validation NON consultée.\n")
            continue

        print(f"  -> Candidat retenu : {best_candidate} (espérance entraînement {best_esperance:.4f}R) — passage en VALIDATION (un seul essai).")
        winners[hyp_key] = best_candidate

        with _apply_candidate(cfg["module"], dict(cfg["candidates"][best_candidate])):
            n_val, esp_val, per_asset_val = _run_period(hyp_key, validation_bars, validation_confirming)
        esp_val_str = f"{esp_val:.4f}R" if esp_val is not None else "N/A"
        passed = esp_val is not None and esp_val > 0 and n_val >= PHASE_A_MIN_TRADES_BACKTEST
        print(f"  === VALIDATION {hyp_key} candidat {best_candidate} : n={n_val} espérance={esp_val_str} -> {'PASS' if passed else 'FAIL'} ===")
        for asset, (an, ae) in per_asset_val.items():
            ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
            print(f"      {asset}: n={an} espérance={ae_str}")
        print()

    print("Terminé.")


if __name__ == "__main__":
    main()
