"""
_h2_funnel_audit.py — Chantier 3 (docs/Prompts_Chantiers_2-6.md) : audit
MÉCANIQUE du déclencheur de l'Hypothèse #2, diagnostic uniquement.
Aucune écriture DB, aucun appel réseau, aucun paramètre modifié.

Instrumente src.ict_strategy (réutilisée telle quelle par
hypothesis2_strategy.evaluate_entry) pour compter, sur l'historique M15
complet des 8 actifs :
  1. Chaque condition élémentaire, isolément ET en conjonction
     cumulative, DANS L'ORDRE où le code les évalue (voir
     ict_strategy._find_regime_and_leg / _evaluate_entry) :
       C1 fenêtre suffisante (>= RECENT_WINDOW + 2*FRACTAL_K + 1)
       C2 swings hauts ET bas confirmés tous deux présents
       C3 régime structurel résolu (BOS dans un sens ou l'autre)
       C4 jambe d'impulsion valide trouvée dans le sens du régime
       C5 clôture courante dans la zone de Fibonacci 61,8-78,6%
       C6 FVG chevauchant la zone, même sens que le régime (= signal)
     Ces conditions sont structurellement IMBRIQUÉES dans le code (C(n)
     ne peut être évaluée que si C(n-1) est vraie : la zone de Fibonacci
     n'existe pas sans jambe, la jambe n'existe pas sans régime) — la
     notion d'"isolément" pour C3-C6 est donc bornée à "parmi les bougies
     qui atteignent cette étape", pas un masque indépendant sur
     l'historique complet. Ce constat structurel est lui-même rapporté.
  2. Signaux GÉNÉRÉS (C6 vrai) vs trades COMPLÉTÉS (via un rejeu réel de
     backtest_engine.replay_hypothesis, qui n'appelle entry_fn que
     lorsqu'aucune position n'est déjà ouverte) — l'écart mesure
     précisément ce que le garde-fou "une position à la fois" absorbe.

Usage :
    python scripts/_h2_funnel_audit.py
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import DEFAULT_LOOKBACK, HistoricalBar, bar_from_raw, replay_hypothesis
from src.hypothesis2_strategy import evaluate_entry as h2_evaluate_entry
from src.ict_strategy import (
    FRACTAL_K,
    RECENT_WINDOW,
    compute_fibonacci_zone,
    compute_structural_regime,
    find_confirmed_swings,
    find_fvgs,
)
from src.market_data import Candle
from src.risk_engine import RiskCaps, RiskEngine

HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
ASSETS = list(ASSET_WHITELIST.keys())
RESOLUTION = "MINUTE_15"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75

WINDOW_SIZE = RECENT_WINDOW + 2 * FRACTAL_K + 1  # 25, voir ict_strategy._find_regime_and_leg


def load_bars(epic: str) -> List[HistoricalBar]:
    raw = json.loads((HISTORICAL_DIR / f"{epic}_{RESOLUTION}.json").read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw) if b is not None]


def funnel_stage(candles: List[Candle]) -> str:
    """Reproduit EXACTEMENT ict_strategy._find_regime_and_leg puis
    _evaluate_entry, étape par étape, en rapportant la première étape où
    ça s'arrête. Aucune logique nouvelle : copie fidèle pour
    instrumentation seule."""
    if len(candles) < WINDOW_SIZE:
        return "C0_fenetre_insuffisante"
    recent = candles[-WINDOW_SIZE:]

    swing_highs, swing_lows = find_confirmed_swings(recent, FRACTAL_K)
    if not swing_highs or not swing_lows:
        return "C1_pas_de_swings_des_deux_cotes"

    current_close = candles[-1].close
    regime = compute_structural_regime(swing_highs, swing_lows, current_close)
    if regime is None:
        return "C2_pas_de_regime_resolu"

    if regime == "long":
        candidates = [(i, p) for i, p in swing_highs if i > swing_lows[-1][0] and p > swing_lows[-1][1]]
        leg = (swing_lows[-1][1], candidates[0][1]) if candidates else None
    else:
        candidates = [(i, p) for i, p in swing_lows if i > swing_highs[-1][0] and p < swing_highs[-1][1]]
        leg = (candidates[0][1], swing_highs[-1][1]) if candidates else None
    if leg is None:
        return "C3_pas_de_jambe_valide"
    swing_low, swing_high = leg

    zone_low, zone_high = compute_fibonacci_zone(regime, swing_low, swing_high)
    if not (zone_low <= current_close <= zone_high):
        return "C4_hors_zone_fibonacci"

    fvgs = find_fvgs(recent)
    overlap = any(d == regime and not (high < zone_low or low > zone_high) for d, low, high in fvgs)
    if not overlap:
        return "C5_pas_de_fvg_chevauchant"

    return "C6_signal_genere"


STAGES = [
    "C0_fenetre_insuffisante", "C1_pas_de_swings_des_deux_cotes", "C2_pas_de_regime_resolu",
    "C3_pas_de_jambe_valide", "C4_hors_zone_fibonacci", "C5_pas_de_fvg_chevauchant", "C6_signal_genere",
]
CUMULATIVE_ORDER = STAGES[1:]  # a partir de C1 : chaque etape suivante est un sur-ensemble de la precedente


def audit_asset(asset: str, bars: List[HistoricalBar]) -> Dict[str, int]:
    candles = [b.to_candle() for b in bars]
    counts = {s: 0 for s in STAGES}
    n = len(candles)
    for t in range(n):
        window = candles[max(0, t + 1 - DEFAULT_LOOKBACK): t + 1]
        stage = funnel_stage(window)
        counts[stage] += 1
    counts["_total_bougies"] = n
    return counts


def cumulative_from_stage_counts(counts: Dict[str, int]) -> Dict[str, int]:
    """Compte, pour chaque etape, le nombre de bougies qui ONT ATTEINT
    au moins cette etape (cumulatif, decroissant) — pas seulement celles
    qui s'y sont arretees."""
    reached = {}
    remaining = sum(counts[s] for s in CUMULATIVE_ORDER)
    for s in CUMULATIVE_ORDER:
        reached[s] = remaining
        remaining -= counts[s]
    return reached


def audit_position_gate(asset: str, bars: List[HistoricalBar]) -> Tuple[int, int, int]:
    """Rejoue reellement backtest_engine.replay_hypothesis (garde-fou
    'une position a la fois' INCLUS) en enveloppant h2_evaluate_entry
    pour compter EXACTEMENT les bougies ou l'entree a ete appelee (donc
    PAS bloquee par une position deja ouverte). Retourne
    (bougies_libres_evaluees, signaux_generes_pendant_periode_libre,
    trades_completes_reels)."""
    free_bars_evaluated = 0
    free_signals = 0

    def wrapped_entry(asset_name, window):
        nonlocal free_bars_evaluated, free_signals
        free_bars_evaluated += 1
        result = h2_evaluate_entry(asset_name, window)
        if result is not None:
            free_signals += 1
        return result

    risk_engine = RiskEngine(
        caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL),
        whitelist=ASSET_WHITELIST,
    )
    result = replay_hypothesis(
        asset, bars, wrapped_entry, risk_engine, ASSET_WHITELIST,
        ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, require_regime_confirmation=False,
        confirming_bars=None, is_donchian_trailing=False,
    )
    return free_bars_evaluated, free_signals, len(result.trades)


def main() -> None:
    print("Chantier 3 — Audit mecanique du declencheur H2 (diagnostic seul, aucune ecriture DB).\n")
    print(f"WINDOW_SIZE (ict_strategy) = {WINDOW_SIZE} bougies M15 ; FRACTAL_K={FRACTAL_K} ; RECENT_WINDOW={RECENT_WINDOW}\n")

    all_counts: Dict[str, int] = {s: 0 for s in STAGES}
    per_asset_cumulative: Dict[str, Dict[str, int]] = {}
    per_asset_gate: Dict[str, Tuple[int, int, int]] = {}

    for asset in ASSETS:
        bars = load_bars(asset)
        counts = audit_asset(asset, bars)
        for s in STAGES:
            all_counts[s] += counts[s]
        cum = cumulative_from_stage_counts(counts)
        per_asset_cumulative[asset] = {"_total_bougies": counts["_total_bougies"], **cum}
        per_asset_gate[asset] = audit_position_gate(asset, bars)
        print(f"{asset}: {counts['_total_bougies']} bougies M15, arret par etape = {counts}")

    print("\n=== Entonnoir CUMULATIF (bougies ayant atteint AU MOINS cette etape), par actif ===")
    header = "| Actif | Total | >=C1 (swings) | >=C2 (regime) | >=C3 (jambe) | >=C4 (zone fib) | >=C5 (fvg dans zone, =C6 signal) |"
    print(header)
    print("|---|---|---|---|---|---|---|")
    pooled_cum = {s: 0 for s in CUMULATIVE_ORDER}
    pooled_total = 0
    for asset in ASSETS:
        c = per_asset_cumulative[asset]
        pooled_total += c["_total_bougies"]
        for s in CUMULATIVE_ORDER:
            pooled_cum[s] += c[s]
        print(f"| {asset} | {c['_total_bougies']} | {c['C1_pas_de_swings_des_deux_cotes']} | {c['C2_pas_de_regime_resolu']} | {c['C3_pas_de_jambe_valide']} | {c['C4_hors_zone_fibonacci']} | {c['C5_pas_de_fvg_chevauchant']} |")
    print(f"| **POOLED** | {pooled_total} | {pooled_cum['C1_pas_de_swings_des_deux_cotes']} | {pooled_cum['C2_pas_de_regime_resolu']} | {pooled_cum['C3_pas_de_jambe_valide']} | {pooled_cum['C4_hors_zone_fibonacci']} | {pooled_cum['C5_pas_de_fvg_chevauchant']} |")

    print("\n(Note : les libelles de colonne '>=C1' etc. designent des noms d'etapes internes a ce")
    print(" script ; '>=C1' = a passe le controle de fenetre ET a des swings des deux cotes, etc.")
    print(" La derniere colonne (>=C5) EST le nombre de signaux generes, C6.)\n")

    print("=== Garde-fou 'une position a la fois' : signaux generes (periode libre) vs trades completes ===")
    print("| Actif | Bougies evaluees (libres) | Signaux generes (libre) | Trades completes (reel) | Signaux 'perdus' en trade (perte fill/gestion) |")
    print("|---|---|---|---|---|")
    total_free_signals = 0
    total_completed = 0
    for asset in ASSETS:
        free_bars, free_signals, completed = per_asset_gate[asset]
        lost = free_signals - completed
        total_free_signals += free_signals
        total_completed += completed
        print(f"| {asset} | {free_bars} | {free_signals} | {completed} | {lost} |")
    print(f"| **POOLED** | — | {total_free_signals} | {total_completed} | {total_free_signals - total_completed} |")

    print("\n=== Signaux bruts (sans le garde-fou position) vs signaux pendant periode libre ===")
    print("| Actif | Signaux bruts (C6, position ignoree) | Signaux periode libre (garde-fou applique) | Bloques par position deja ouverte |")
    print("|---|---|---|---|")
    total_raw = 0
    total_blocked = 0
    for asset in ASSETS:
        raw_c6 = per_asset_cumulative[asset]["C5_pas_de_fvg_chevauchant"]
        free_bars, free_signals, completed = per_asset_gate[asset]
        blocked = raw_c6 - free_signals
        total_raw += raw_c6
        total_blocked += blocked
        print(f"| {asset} | {raw_c6} | {free_signals} | {blocked} |")
    print(f"| **POOLED** | {total_raw} | {total_free_signals} | {total_blocked} |")

    print("\nTermine. Aucune ecriture DB, aucun appel reseau, aucun parametre modifie.")


if __name__ == "__main__":
    main()
