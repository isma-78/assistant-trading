"""
evaluate_hypothesis_new_tools_cycle.py — Outil de recherche PONCTUEL
(même statut que calibrate_pip_value.py / evaluate_hypothesis_candidates.py
/ evaluate_hypothesis_timeframe_cycle.py), cycle 3 de l'évolution H4/H5,
pré-enregistré dans docs/HYPOTHESES.md (25/08/2026, section "cycle 3" —
candidats, budget de variables, correction statistique et critères y
sont figés AVANT ce script). H1/H2/H3 hors périmètre ce cycle.

Ne fait AUCUN appel réseau et n'écrit dans AUCUNE base de données —
lecture seule sur les fichiers JSON déjà téléchargés (data/historical/),
calcul en mémoire, impression du rapport sur stdout. Les candidats B
(H4/H5) sont des NOUVELLES fonctions d'entrée définies ICI (pas dans les
modules de stratégie réels) : elles introduisent une condition
supplémentaire par rapport à `evaluate_entry` existant, jamais une
simple valeur — voir docs/HYPOTHESES.md pour la classification
évolution/nouvelle-hypothèse (les deux candidats B sont "nouvelle
hypothèse", jamais auto-déployables même s'ils valident).

Méthode (voir docs/HYPOTHESES.md pour le détail complet) :
1. Pour H4 et H5, 2 candidats chacun (A=référence actuelle, B=A + une
   variable supplémentaire), rejoués sur l'ENTRAÎNEMENT seul (< CUTOFF).
2. Qualification : n >= PHASE_B_MIN_TRADES_BACKTEST ET (moyenne - z*SE) > 0,
   z corrigé Bonferroni (m=2 par hypothèse, alpha global 0.05).
3. Le qualifiant (au plus un par hypothèse) est rejoué UNE FOIS sur la
   VALIDATION seule. PASS si espérance > 0 ET n >= PHASE_A_MIN_TRADES_BACKTEST.

Usage :
    python scripts/evaluate_hypothesis_new_tools_cycle.py
"""

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis5_strategy as h5_mod
import src.ict_strategy as ict
import src.mean_reversion_strategy as h4_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.confidence_scorer import PHASE_A_MIN_TRADES_BACKTEST, PHASE_B_MIN_TRADES_BACKTEST
from src.market_data import Candle
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import compute_regime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

CUTOFF = "2025-12-01T00:00:00"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0

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
    train = [b for b in bars if b.time_utc < cutoff]
    validation = [b for b in bars if b.time_utc >= cutoff]
    return train, validation


# ---------------------------------------------------------------------------
# H4 candidat B : régime MA200 + toucher de bande de Bollinger (INCHANGÉ,
# voir mean_reversion_strategy._evaluate_entry) + confluence RSI(14) au
# toucher (30/70, convention standard, a priori) — voir docs/HYPOTHESES.md.
# ---------------------------------------------------------------------------

def h4_entry_with_rsi_confluence(asset: str, candles: List[Candle]) -> Optional[h4_mod.MeanReversionSignal]:
    try:
        regime = compute_regime(candles)
        if regime is None:
            return None
        bands = h4_mod.compute_bollinger_bands(candles)
        if bands is None:
            return None
        upper, middle, lower = bands
        half_width = (upper - lower) / 2
        if half_width <= 0:
            return None
        current_close = candles[-1].close
        rsi = h5_mod.compute_rsi(candles, period=14)
        if rsi is None:
            return None

        if regime == "long" and current_close <= lower and rsi < RSI_OVERSOLD:
            stop_price = current_close - h4_mod.STOP_WIDTH_MULTIPLIER * half_width
            return h4_mod.MeanReversionSignal(
                asset=asset, direction="long", entry_price=current_close,
                stop_price=stop_price, take_profit=middle,
            )
        if regime == "short" and current_close >= upper and rsi > RSI_OVERBOUGHT:
            stop_price = current_close + h4_mod.STOP_WIDTH_MULTIPLIER * half_width
            return h4_mod.MeanReversionSignal(
                asset=asset, direction="short", entry_price=current_close,
                stop_price=stop_price, take_profit=middle,
            )
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# H5 candidat B : régime structurel + RSI(14) franchissant 50 (INCHANGÉ,
# voir hypothesis5_strategy._evaluate_entry) + confluence ICT complète
# (zone Fibonacci + FVG chevauchant, EXACTEMENT la logique de
# ict_strategy._evaluate_entry, Hypothèse #2) — voir docs/HYPOTHESES.md.
# ---------------------------------------------------------------------------

def h5_entry_with_ict_confluence(asset: str, candles: List[Candle]) -> Optional[h5_mod.Hypothesis5Signal]:
    try:
        result = ict._find_regime_and_leg(candles)
        if result is None:
            return None
        regime, swing_low, swing_high, current_close = result

        zone_low, zone_high = ict.compute_fibonacci_zone(regime, swing_low, swing_high)
        if not (zone_low <= current_close <= zone_high):
            return None

        window_size = ict.RECENT_WINDOW + 2 * ict.FRACTAL_K + 1
        recent = candles[-window_size:]
        fvgs = ict.find_fvgs(recent)
        overlap = any(d == regime and not (high < zone_low or low > zone_high) for d, low, high in fvgs)
        if not overlap:
            return None

        if not h5_mod._rsi_just_crossed_threshold(candles, regime):
            return None

        stop_price = swing_low if regime == "long" else swing_high
        tp1, tp2 = h5_mod._compute_tp_levels(regime, current_close, stop_price)
        return h5_mod.Hypothesis5Signal(
            asset=asset, direction=regime, entry_price=current_close,
            stop_price=stop_price, tp1=tp1, tp2=tp2,
        )
    except Exception:
        return None


HYPOTHESES = {
    "H4": {
        "module": h4_mod, "require_regime": True, "is_donchian": False,
        "candidates": {"A": h4_mod.evaluate_entry, "B": h4_entry_with_rsi_confluence},
    },
    "H5": {
        "module": h5_mod, "require_regime": False, "is_donchian": False,
        "candidates": {"A": h5_mod.evaluate_entry, "B": h5_entry_with_ict_confluence},
    },
}


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _bonferroni_z(m: int) -> float:
    """Identique à evaluate_hypothesis_timeframe_cycle.py::_bonferroni_z
    (approximation Abramowitz & Stegun 26.2.23) — dupliquée ici plutôt que
    partagée entre deux scripts de recherche ponctuels, cohérent avec leur
    statut (jamais importés l'un par l'autre)."""
    alpha = 0.05 / m
    p = 1 - alpha
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    z = t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)
    return z


def _run_period(hyp_key: str, entry_fn, own_period: Dict[str, List[HistoricalBar]], confirming_period: Optional[Dict[str, List[HistoricalBar]]]) -> tuple:
    cfg = HYPOTHESES[hyp_key]
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
    return n, mean, stdev, per_asset


def main() -> None:
    print(f"Cycle 3 — nouveaux outils (H4/H5 seules). Découpage : ENTRAÎNEMENT < {CUTOFF} | VALIDATION >= {CUTOFF}")
    print("Correction Bonferroni par famille d'hypothèse (m=2, alpha global 0.05).\n")

    train_bars: Dict[str, List[HistoricalBar]] = {}
    validation_bars: Dict[str, List[HistoricalBar]] = {}
    for asset in ALL_ASSETS:
        bars = _load_bars(asset)
        train_bars[asset], validation_bars[asset] = _split(bars, CUTOFF)
    train_confirming = {"US30": train_bars["US30"], "US100": train_bars["US100"]}
    validation_confirming = {"US30": validation_bars["US30"], "US100": validation_bars["US100"]}

    for hyp_key, cfg in HYPOTHESES.items():
        m = len(cfg["candidates"])
        z = _bonferroni_z(m)
        print(f"=== {hyp_key} — {m} candidats, z corrigé = {z:.4f} — ENTRAÎNEMENT ===")
        best_candidate, best_mean = None, None

        for cand_name, entry_fn in cfg["candidates"].items():
            n, mean, stdev, per_asset = _run_period(hyp_key, entry_fn, train_bars, train_confirming)
            if n >= 2 and mean is not None and stdev is not None:
                se = stdev / math.sqrt(n)
                lower_bound = mean - z * se
            else:
                lower_bound = None
            mean_str = f"{mean:.4f}R" if mean is not None else "N/A"
            lb_str = f"{lower_bound:.4f}R" if lower_bound is not None else "N/A"
            qualifies = n >= PHASE_B_MIN_TRADES_BACKTEST and lower_bound is not None and lower_bound > 0
            print(f"  Candidat {cand_name} : n={n} moyenne={mean_str} borne_basse_corrigée={lb_str} {'[QUALIFIE]' if qualifies else ''}")
            for asset, (an, ae) in per_asset.items():
                ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
                print(f"      {asset}: n={an} espérance={ae_str}")
            if qualifies and (best_mean is None or mean > best_mean):
                best_candidate, best_mean = cand_name, mean

        if best_candidate is None:
            print(f"  -> Aucun candidat qualifié (après correction) sur l'entraînement pour {hyp_key} — validation NON consultée.\n")
            continue

        print(f"  -> Candidat retenu : {best_candidate} (moyenne entraînement {best_mean:.4f}R) — passage en VALIDATION (un seul essai).")
        entry_fn = cfg["candidates"][best_candidate]
        n_val, mean_val, _, per_asset_val = _run_period(hyp_key, entry_fn, validation_bars, validation_confirming)
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
