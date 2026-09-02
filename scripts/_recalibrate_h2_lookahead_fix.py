"""
_recalibrate_h2_lookahead_fix.py — Recalibration H2/L2, moteur corrige
(c753ce5), session dediee du 02/09/2026 (voir docs/DECISIONS.md).

Script de recherche PONCTUEL, jamais appele depuis les 6 boucles live,
aucun appel reseau (lit uniquement data/historical/*.json deja en
cache). Ne persiste RIEN en base — imprime les resultats, a copier a la
main dans docs/DECISIONS.md apres lecture humaine/agent (memes
garde-fous que scripts/run_retrospective_backtest.py, mais celui-ci ne
supporte pas le contrat a 3 resolutions de hypothesis2_strategy_v2, ni
les combos de grille).

Grille reprise TELLE QUELLE de docs/HYPOTHESES.md #### H2/L2 (m=24,
pre-enregistree le 29/08/2026, AVANT le bug de lookahead) — seules les
VALEURS de grille sont reutilisees (definition, pas resultat), jamais
un chiffre issu d'un calcul anterieur au commit c753ce5.

CHFJPY exclue (meme motif que le pre-enregistrement, mais pour une
raison verifiee independamment le 02/09/2026 : DAY ne remonte qu'au
2023-12-08 sur ce compte demo, gap qui casse la composition identique
decouverte/confirmation exigee par le pre-enregistrement — DAY est
necessaire ici car c'est la resolution DEPLOYEE en direct pour L2,
distincte de M15/H1/H4 tel qu'ecrit dans le pre-enregistrement, voir
l'entree DECISIONS.md dediee a cet ecart).

Etapes :
  1. TRAIN (2019-01-01 -> 2020-12-31) : replay des 24 combos x 8 actifs,
     floor n>=200 par combo (agregat 8 actifs), court-circuit si aucun
     combo ne depasse ce plancher avec une moyenne BRUTE strictement
     positive, sinon `evolution_engine.select_best_candidate` (Bonferroni
     m=24) choisit le meilleur candidat qualifiant.
  2. HOLDOUT (2021-01-01 -> 2022-12-31) : SEUL le candidat selectionne
     est rejoue sur cette periode disjointe (jamais un second choix),
     floor n>=200, borne basse Bonferroni m=1 (`compute_lower_bound`)
     > 0 requise pour passer — verification "nested", pas une deuxieme
     selection.
  3. CONFIRMATION (2023-01-01 -> 2024-06-14), UN SEUL passage, seulement
     si l'etape 2 passe : Bonferroni sur le compteur CUMULATIF du
     projet (m=30, z=2.9352, fourni par Ismael) applique a la borne
     basse par BOOTSTRAP PAR BLOCS CALENDAIRES
     (`compute_calendar_block_bootstrap_lower_bound`), jamais
     moyenne-z*SE. Controle annee par annee si positif.
"""

import itertools
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis2_strategy_v2 as h2_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import BacktestTrade, HistoricalBar, bar_from_raw, replay_hypothesis
from src.config import load_config
from src.evolution_engine import (
    TrainingResult,
    bonferroni_one_sided_z,
    compute_calendar_block_bootstrap_lower_bound,
    compute_lower_bound,
    select_best_candidate,
)
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"

ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]  # CHFJPY exclue, voir docstring

TRAIN_START, TRAIN_END = "2019-01-01T00:00:00", "2020-12-31T23:59:59"
HOLDOUT_START, HOLDOUT_END = "2021-01-01T00:00:00", "2022-12-31T23:59:59"
CONFIRM_START, CONFIRM_END = "2023-01-01T00:00:00", "2024-06-14T23:59:59"
BUFFER_START = "2018-10-01T00:00:00"  # marge de warmup indicateurs (max besoin=100h, tres large)

# Grille m=24, docs/HYPOTHESES.md #### H2/L2 (29/08/2026) — valeurs reprises telles quelles.
EMA_PERIODS = [20, 50, 100]
RSI_THRESHOLDS = [50.0, 55.0]
N_TFS = [2, 3]
SCORE_THRESHOLDS = [2 / 3, 1.0]

MIN_TRADES = 200
CUMULATIVE_M = 30  # 29 essais avant ce chantier + cet essai de confirmation unique
CUMULATIVE_Z = 2.9352  # fourni par Ismael, verifie ci-dessous par bonferroni_one_sided_z(30)

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(asset: str, resolution: str) -> List[HistoricalBar]:
    key = f"{asset}_{resolution}"
    if key in _bar_cache:
        return _bar_cache[key]
    path = HISTORICAL_DIR / f"{key}.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[key] = bars
    return bars


def _slice(bars: List[HistoricalBar], start: str, end: str) -> List[HistoricalBar]:
    return [b for b in bars if start <= b.time_utc <= end]


def _make_risk_engine(config) -> Tuple[RiskEngine, dict]:
    caps = RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=config.envelope_initial)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST), ASSET_WHITELIST


def _run_combo(config, asset: str, own_bars_full: List[HistoricalBar], hour4: List[HistoricalBar], day: List[HistoricalBar],
                combo: Tuple[int, float, int, float], window_start: str, window_end: str) -> List[BacktestTrade]:
    ema_period, rsi_threshold, n_tf, score_threshold = combo
    h2_mod.EMA_PERIOD = ema_period
    h2_mod.RSI_THRESHOLD = rsi_threshold
    h2_mod.N_TF = n_tf
    h2_mod.SCORE_THRESHOLD = score_threshold

    own_bars = _slice(own_bars_full, BUFFER_START, window_end)
    if len(own_bars) < 50:
        return []
    risk_engine, whitelist = _make_risk_engine(config)
    result = replay_hypothesis(
        asset, own_bars, h2_mod.evaluate_entry, risk_engine, whitelist,
        config.envelope_initial, config.confidence_threshold,
        lookback=220,
        extra_resolution_bars={"HOUR_4": hour4, "DAY": day},
        own_bar_duration_seconds=3600.0,
        extra_resolution_seconds={"HOUR_4": 14400.0, "DAY": 86400.0},
    )
    return [t for t in result.trades if window_start <= t.entry_time_utc <= window_end]


def main() -> None:
    config = load_config()
    print(f"=== Recalibration H2/L2 — moteur corrige, {len(ASSETS)} actifs (CHFJPY exclue) ===")
    print(f"Verification z fourni : bonferroni_one_sided_z(24)={bonferroni_one_sided_z(24):.4f}, "
          f"bonferroni_one_sided_z(1)={bonferroni_one_sided_z(1):.4f}, "
          f"bonferroni_one_sided_z({CUMULATIVE_M})={bonferroni_one_sided_z(CUMULATIVE_M):.4f} (attendu {CUMULATIVE_Z})")

    print("\nChargement des bougies en cache (HOUR/HOUR_4/DAY, 8 actifs)...")
    bars_hour = {a: _load_bars(a, "HOUR") for a in ASSETS}
    bars_hour4 = {a: _load_bars(a, "HOUR_4") for a in ASSETS}
    bars_day = {a: _load_bars(a, "DAY") for a in ASSETS}

    combos = list(itertools.product(EMA_PERIODS, RSI_THRESHOLDS, N_TFS, SCORE_THRESHOLDS))
    assert len(combos) == 24, f"grille attendue m=24, obtenue {len(combos)}"

    # --- ETAPE 1 : TRAIN ---
    print(f"\n=== ETAPE 1 — TRAIN {TRAIN_START[:10]} -> {TRAIN_END[:10]} ===")
    train_results: List[TrainingResult] = []
    train_trades_by_combo: Dict[Tuple, List[BacktestTrade]] = {}
    for combo in combos:
        all_trades: List[BacktestTrade] = []
        for asset in ASSETS:
            all_trades.extend(_run_combo(
                config, asset, bars_hour[asset], bars_hour4[asset], bars_day[asset],
                combo, TRAIN_START, TRAIN_END,
            ))
        r_values = [t.r_multiple_total for t in all_trades]
        n = len(r_values)
        raw_mean = statistics.fmean(r_values) if r_values else None
        train_trades_by_combo[combo] = all_trades
        name = f"EMA={combo[0]}/RSI={combo[1]}/N_TF={combo[2]}/SCORE={combo[3]:.4f}"
        train_results.append(TrainingResult(candidate=name, n_trades=n, r_values=tuple(r_values)))
        flag = "" if (n >= MIN_TRADES and raw_mean is not None and raw_mean > 0) else "  [hors plancher/negatif]"
        print(f"  {name:45s} n={n:5d} raw_mean={raw_mean if raw_mean is not None else float('nan'):+.4f}R{flag}")

    qualifying_positive = [r for r in train_results if r.n_trades >= MIN_TRADES and r.mean_r is not None and r.mean_r > 0]
    if not qualifying_positive:
        print("\nCOURT-CIRCUIT : aucun combo n>=200 avec moyenne BRUTE strictement positive sur TRAIN.")
        print("H2 CLOSE cote recherche (negatif) — CV/gate/confirmation non executes, fenetre 2023-2024.06 NON touchee.")
        return

    selection = select_best_candidate(train_results, min_trades=MIN_TRADES, family_alpha=0.05)
    print(f"\nselect_best_candidate (Bonferroni m={len(train_results)}, z={selection.z_score:.4f}) : "
          f"selected={selection.selected!r}")
    print(f"  raison : {selection.reason}")
    print(f"  qualifiants : {selection.qualifying}")
    if selection.selected is None:
        print("\nAucun candidat ne franchit le gate TRAIN (borne basse Bonferroni). "
              "H2 CLOSE cote recherche — fenetre 2023-2024.06 NON touchee.")
        return

    selected_combo = combos[[r.candidate for r in train_results].index(selection.selected)]

    # --- ETAPE 2 : HOLDOUT (nested check, un seul candidat) ---
    print(f"\n=== ETAPE 2 — HOLDOUT {HOLDOUT_START[:10]} -> {HOLDOUT_END[:10]} (candidat unique : {selection.selected}) ===")
    holdout_trades: List[BacktestTrade] = []
    for asset in ASSETS:
        holdout_trades.extend(_run_combo(
            config, asset, bars_hour[asset], bars_hour4[asset], bars_day[asset],
            selected_combo, HOLDOUT_START, HOLDOUT_END,
        ))
    holdout_r = [t.r_multiple_total for t in holdout_trades]
    n_holdout = len(holdout_r)
    z1 = bonferroni_one_sided_z(1)
    lb_holdout = compute_lower_bound(holdout_r, z1) if n_holdout >= 2 else None
    print(f"  n={n_holdout}, raw_mean={(statistics.fmean(holdout_r) if holdout_r else float('nan')):+.4f}R, "
          f"borne basse (z={z1:.4f})={lb_holdout if lb_holdout is not None else 'N/A'}")

    holdout_pass = n_holdout >= MIN_TRADES and lb_holdout is not None and lb_holdout > 0
    if not holdout_pass:
        print("\nHOLDOUT NE QUALIFIE PAS (n<200 ou borne basse<=0). H2 CLOSE cote recherche — "
              "candidat non confirme comme robuste hors periode d'entrainement. "
              "Fenetre 2023-2024.06 NON touchee.")
        return

    print("\nHOLDOUT QUALIFIE. Passage a la CONFIRMATION (essai unique, fenetre 2023-2024.06).")

    # --- ETAPE 3 : CONFIRMATION (essai unique) ---
    print(f"\n=== ETAPE 3 — CONFIRMATION {CONFIRM_START[:10]} -> {CONFIRM_END[:10]} (candidat : {selection.selected}) ===")
    confirm_trades: List[BacktestTrade] = []
    for asset in ASSETS:
        confirm_trades.extend(_run_combo(
            config, asset, bars_hour[asset], bars_hour4[asset], bars_day[asset],
            selected_combo, CONFIRM_START, CONFIRM_END,
        ))
    confirm_r = [t.r_multiple_total for t in confirm_trades]
    confirm_ts = [t.entry_time_utc for t in confirm_trades]
    n_confirm = len(confirm_r)
    raw_mean_confirm = statistics.fmean(confirm_r) if confirm_r else None
    z_cum = bonferroni_one_sided_z(CUMULATIVE_M)
    lb_confirm = compute_calendar_block_bootstrap_lower_bound(confirm_r, confirm_ts, confidence=0.95) if confirm_r else None

    print(f"  n={n_confirm}, raw_mean={(raw_mean_confirm if raw_mean_confirm is not None else float('nan')):+.4f}R")
    print(f"  Bonferroni cumulatif m={CUMULATIVE_M}, z={z_cum:.4f} (repere Ismael={CUMULATIVE_Z})")
    print(f"  borne basse bootstrap par blocs calendaires (95%) = {lb_confirm if lb_confirm is not None else 'N/A'}")

    verdict_pass = n_confirm >= MIN_TRADES and lb_confirm is not None and lb_confirm > 0
    print(f"\nVERDICT CONFIRMATION : {'QUALIFIE' if verdict_pass else 'NE QUALIFIE PAS'} "
          f"(n>=200 et borne basse bootstrap > 0)")

    if verdict_pass:
        by_year: Dict[str, List[float]] = {}
        for r, ts in zip(confirm_r, confirm_ts):
            by_year.setdefault(ts[:4], []).append(r)
        print("  Controle annee par annee :")
        for year, values in sorted(by_year.items()):
            print(f"    {year}: n={len(values)} mean={statistics.fmean(values):+.4f}R")

    print("\nFENETRE 2019-2024.06 : essai de confirmation DEPENSE (cet appel) — a marquer close "
          "definitivement dans docs/DECISIONS.md quel que soit le verdict ci-dessus.")


if __name__ == "__main__":
    main()
