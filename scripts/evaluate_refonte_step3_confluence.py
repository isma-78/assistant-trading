"""
evaluate_refonte_step3_confluence.py — Phase 2, étape 3 (priorité #3,
dernière) du chantier de refonte H2-H5 (docs/HYPOTHESES.md, 25/08/2026).
Branche A (H3, H5), appliquée PAR-DESSUS le stop ATR de l'étape 2
(résolution MINUTE_15, retenue à l'étape 1 — aucun changement).

Retire UNE confluence par hypothèse (la plus "ajoutée", pas le
déclencheur fondamental) et compare, sur l'ENTRAÎNEMENT seul :
- H3 : confirmation de régime croisée (US30/US100, `require_regime_
  confirmation`) — retirée ou conservée. Le déclencheur (MA200 + rupture
  Donchian(20)) et le stop ATR (étape 2) restent identiques dans les
  deux cas — seul `require_regime_confirmation` change à l'appel de
  `replay_hypothesis`, aucune nouvelle fonction d'entrée.
- H5 : filtre RSI(14)/50 — retiré (entrée structurelle PURE, réutilise
  `ict_strategy.compute_structural_entry` tel quel) ou conservé (étape
  2 : structure + RSI + stop ATR).

Règle d'acceptation (pré-enregistrée, avant tout calcul) : la confluence
n'est retirée QUE SI le nombre de signaux augmente ET que l'espérance ne
se dégrade pas (moyenne sans confluence >= moyenne avec). Aucune
confluence n'est jamais AJOUTÉE. Le résultat de cette étape est le
CANDIDAT FINAL de la Phase 2 pour chaque hypothèse — validé une seule
fois séparément après ce script (jamais ici).

Aucune écriture DB, aucun appel réseau.

Usage :
    python scripts/evaluate_refonte_step3_confluence.py
"""

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ict_strategy as ict
from src import hypothesis5_strategy as h5_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.market_data import Candle, compute_atr
from src.risk_engine import RiskCaps, RiskEngine

# Réutilise les fonctions d'entrée ATR de l'étape 2 (pas redupliquées).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_refonte_step2_atr_stop import (  # noqa: E402
    make_h3_atr_entry, make_h5_atr_entry, PHASE1_COST,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

CUTOFF = "2025-12-01T00:00:00"
ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

# Multiples calibrés à l'étape 2 (recopiés depuis logs/refonte_step2.log
# — pas recalculés ici, mêmes valeurs que le candidat déjà évalué).
H3_ATR_MULTIPLE = 20.0
H5_ATR_MULTIPLE = 19.0

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str) -> List[HistoricalBar]:
    if epic in _bar_cache:
        return _bar_cache[epic]
    path = HISTORICAL_DIR / f"{epic}_MINUTE_15.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[epic] = bars
    return bars


def _split(bars: List[HistoricalBar], cutoff: str) -> tuple:
    return [b for b in bars if b.time_utc < cutoff], [b for b in bars if b.time_utc >= cutoff]


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def make_h5_atr_entry_no_rsi(atr_multiple: float):
    """H5 SANS le filtre RSI — entrée structurelle pure (réutilise
    `ict_strategy.compute_structural_entry` telle quelle) + stop ATR
    (même logique que make_h5_atr_entry, RSI en moins)."""
    def _entry(asset: str, candles: List[Candle]) -> Optional[h5_mod.Hypothesis5Signal]:
        try:
            structural_signal = ict.compute_structural_entry(asset, candles)
            if structural_signal is None:
                return None
            atr = compute_atr(candles, period=14)
            if atr is None or atr <= 0:
                return None
            if structural_signal.direction == "long":
                stop_price = structural_signal.entry_price - atr_multiple * atr
            else:
                stop_price = structural_signal.entry_price + atr_multiple * atr
            tp1, tp2 = h5_mod._compute_tp_levels(structural_signal.direction, structural_signal.entry_price, stop_price)
            return h5_mod.Hypothesis5Signal(
                asset=asset, direction=structural_signal.direction, entry_price=structural_signal.entry_price,
                stop_price=stop_price, tp1=tp1, tp2=tp2,
            )
        except Exception:
            return None
    return _entry


def _run_train(entry_fn, require_regime: bool) -> tuple:
    risk_engine = _make_risk_engine()
    all_r: List[float] = []
    per_asset = {}
    for asset in ALL_ASSETS:
        train, _ = _split(_load_bars(asset), CUTOFF)
        confirming_bars = None
        if require_regime:
            us30_train, _ = _split(_load_bars("US30"), CUTOFF)
            us100_train, _ = _split(_load_bars("US100"), CUTOFF)
            confirming_bars = {"US30": us30_train, "US100": us100_train}
        result = replay_hypothesis(
            asset, train, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
            require_regime_confirmation=require_regime, confirming_bars=confirming_bars,
        )
        r_values = [t.r_multiple_total for t in result.trades]
        all_r.extend(r_values)
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)
    n = len(all_r)
    mean = statistics.fmean(all_r) if all_r else None
    return n, mean, per_asset


def main() -> None:
    print("Phase 2, étape 3 — réduction de confluence (H3, H5 — branche A), par-dessus le stop ATR de l'étape 2.\n")

    print("=== H3 — confirmation de régime croisée (US30/US100) ===")
    h3_entry = make_h3_atr_entry(H3_ATR_MULTIPLE)
    n_with, mean_with, per_asset_with = _run_train(h3_entry, require_regime=True)
    n_without, mean_without, per_asset_without = _run_train(h3_entry, require_regime=False)
    mean_with_str = f"{mean_with:.4f}R" if mean_with is not None else "N/A"
    mean_without_str = f"{mean_without:.4f}R" if mean_without is not None else "N/A"
    print(f"  AVEC confirmation croisée : n={n_with} moyenne={mean_with_str}")
    print(f"  SANS confirmation croisée : n={n_without} moyenne={mean_without_str}")
    h3_remove = n_without > n_with and mean_without is not None and mean_with is not None and mean_without >= mean_with
    print(f"  -> Confluence retirée ? {'OUI' if h3_remove else 'NON'} (règle : plus de signaux ET espérance non dégradée)\n")

    print("=== H5 — filtre RSI(14)/50 ===")
    h5_entry_with_rsi = make_h5_atr_entry(H5_ATR_MULTIPLE)
    h5_entry_without_rsi = make_h5_atr_entry_no_rsi(H5_ATR_MULTIPLE)
    n_with5, mean_with5, per_asset_with5 = _run_train(h5_entry_with_rsi, require_regime=False)
    n_without5, mean_without5, per_asset_without5 = _run_train(h5_entry_without_rsi, require_regime=False)
    mean_with5_str = f"{mean_with5:.4f}R" if mean_with5 is not None else "N/A"
    mean_without5_str = f"{mean_without5:.4f}R" if mean_without5 is not None else "N/A"
    print(f"  AVEC RSI : n={n_with5} moyenne={mean_with5_str}")
    print(f"  SANS RSI : n={n_without5} moyenne={mean_without5_str}")
    h5_remove = n_without5 > n_with5 and mean_without5 is not None and mean_with5 is not None and mean_without5 >= mean_with5
    print(f"  -> Confluence retirée ? {'OUI' if h5_remove else 'NON'} (règle : plus de signaux ET espérance non dégradée)\n")

    print("=== Candidats finaux (Phase 2, avant validation) ===")
    print(f"  H3 : régime MA200 + Donchian(20) + stop ATR×{H3_ATR_MULTIPLE} + confirmation croisée {'RETIRÉE' if h3_remove else 'CONSERVÉE'}")
    print(f"       n(train)={n_without if h3_remove else n_with} moyenne={(mean_without if h3_remove else mean_with):.4f}R")
    print(f"  H5 : régime structurel + stop ATR×{H5_ATR_MULTIPLE} + RSI {'RETIRÉ' if h5_remove else 'CONSERVÉ'}")
    print(f"       n(train)={n_without5 if h5_remove else n_with5} moyenne={(mean_without5 if h5_remove else mean_with5):.4f}R")

    print("\nTerminé.")


if __name__ == "__main__":
    main()
