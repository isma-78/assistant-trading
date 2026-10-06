"""
evolution_v2_test.py — Moteur du walk-forward des 4 candidates V2
(06/10/2026), implémentation littérale de
`docs/PROTOCOLE_EVOLUTION_V2_06-10.md` §5 (pré-enregistré AVANT tout
calcul). Calcul pur, aucune I/O, aucun appel broker — l'orchestration des
rejeux (`backtest_engine.replay_hypothesis`) vit dans
`scripts/_evolution_v2_walkforward_06-10.py`.
"""

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, stdev
from typing import Dict, List, Optional, Sequence, Tuple

N_MIN_TRAIN = 200
MATCH_TOLERANCE_HOURS = 2
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 20261006
BONFERRONI_M = 4


@dataclass(frozen=True)
class TradeRef:
    """Projection minimale d'un `BacktestTrade` (asset/direction/horodatage
    d'entrée/R) — découplée de `backtest_engine` pour que ce module reste
    testable sans construire de vrais `BacktestTrade`."""
    asset: str
    direction: str
    entry_time_utc: str
    r_multiple: float


def _parse(ts: str):
    from datetime import datetime
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def select_best_grid_value(
    variant_train_results: Dict[object, Tuple[int, Optional[float]]], n_min: int = N_MIN_TRAIN,
) -> Optional[object]:
    """§5 : élimine les variantes à n < `n_min` sur l'apprentissage, retient
    parmi les survivantes celle d'espérance nette BRUTE la plus élevée
    (jamais une différence vs baseline à ce stade — anti-fuite). `None` si
    aucune variante ne survit."""
    survivors = {v: mean_r for v, (n, mean_r) in variant_train_results.items() if n >= n_min and mean_r is not None}
    if not survivors:
        return None
    return max(survivors, key=survivors.get)


def pair_candidate_against_baseline(
    baseline: Sequence[TradeRef], candidate: Sequence[TradeRef], tolerance_hours: float = MATCH_TOLERANCE_HOURS,
) -> Tuple[List[float], List[TradeRef], List[TradeRef]]:
    """§5 : pour chaque trade `baseline`, cherche un trade `candidate` de
    même actif/direction, entrée à <= `tolerance_hours` h — `diff_i =
    R_candidate_matché - R_baseline_i`, ou `R_candidate_matché - 0` si
    aucun match (« non-signalé par la candidate = 0 R »). Chaque trade
    candidate n'est utilisé qu'UNE fois (le plus proche en temps d'abord).
    Retourne (diffs appariés à `baseline`, trades baseline non appariés
    [diagnostic : la candidate N'A PAS pris ce trade], trades candidate
    SANS contrepartie baseline [diagnostic, jamais inclus dans les diffs,
    voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §5])."""
    tol = timedelta(hours=tolerance_hours)
    remaining_candidate = list(candidate)
    diffs: List[float] = []
    unmatched_baseline: List[TradeRef] = []
    for base_trade in baseline:
        base_time = _parse(base_trade.entry_time_utc)
        best_idx, best_delta = None, None
        for idx, cand_trade in enumerate(remaining_candidate):
            if cand_trade.asset != base_trade.asset or cand_trade.direction != base_trade.direction:
                continue
            delta = abs(_parse(cand_trade.entry_time_utc) - base_time)
            if delta <= tol and (best_delta is None or delta < best_delta):
                best_idx, best_delta = idx, delta
        if best_idx is not None:
            matched = remaining_candidate.pop(best_idx)
            diffs.append(matched.r_multiple - base_trade.r_multiple)
        else:
            diffs.append(0.0 - base_trade.r_multiple)
            unmatched_baseline.append(base_trade)
    return diffs, unmatched_baseline, remaining_candidate


def _iso_week(entry_time_utc: str) -> Tuple[int, int]:
    year, week, _ = date.fromisoformat(entry_time_utc[:10]).isocalendar()
    return year, week


def one_sample_block_bootstrap_lower_bound(
    diffs_with_time: Sequence[Tuple[str, float]], quantile: float,
    reps: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED,
) -> Optional[float]:
    """Borne basse unilatérale, bootstrap par blocs calendaires (semaine
    ISO de l'horodatage fourni), sur la série `diffs_with_time` =
    [(horodatage, diff)]. `None` si la série est vide."""
    blocks: Dict[Tuple[int, int], List[float]] = defaultdict(list)
    for ts, diff in diffs_with_time:
        blocks[_iso_week(ts)].append(diff)
    keys = list(blocks)
    if not keys:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        sample = [v for _ in keys for v in blocks[keys[rng.randrange(len(keys))]]]
        means.append(mean(sample))
    means.sort()
    return means[min(len(means) - 1, int(quantile * len(means)))]


def compute_mde(s_diff: float, n: int, m_correction: int = BONFERRONI_M, power: float = 0.80) -> Optional[float]:
    """MDE = (z_{1-0.05/m} + z_power) * s_diff / sqrt(n). `None` si n < 2
    (écart-type non défini) — calculé AVANT toute lecture du signe de la
    différence (§5)."""
    if n < 2:
        return None
    from statistics import NormalDist
    z_alpha = NormalDist().inv_cdf(1 - 0.05 / m_correction)
    z_power = NormalDist().inv_cdf(power)
    return (z_alpha + z_power) * s_diff / (n ** 0.5)


@dataclass(frozen=True)
class FoldResult:
    label: str
    n_test: int
    mean_baseline: Optional[float]
    mean_candidate: Optional[float]
    diff: Optional[float]
    s_diff: Optional[float]
    n_unmatched_baseline: int
    n_unmatched_candidate: int


def diffs_with_baseline_time(
    baseline: Sequence[TradeRef], candidate: Sequence[TradeRef],
) -> Tuple[List[Tuple[str, float]], List[TradeRef], List[TradeRef]]:
    """Comme `pair_candidate_against_baseline`, mais associe à chaque
    `diff_i` l'horodatage d'entrée BASELINE correspondant (pour le
    bootstrap par blocs calendaires, qui a besoin d'une date par point)."""
    diffs, unmatched_baseline, unmatched_candidate = pair_candidate_against_baseline(baseline, candidate)
    timed = [(base_trade.entry_time_utc, diff) for base_trade, diff in zip(baseline, diffs)]
    return timed, unmatched_baseline, unmatched_candidate


def evaluate_fold(label: str, baseline: Sequence[TradeRef], candidate: Sequence[TradeRef]) -> FoldResult:
    """Un fold de test (2021 ou 2022) : appariement, moyennes, diff."""
    diffs, unmatched_baseline, unmatched_candidate = pair_candidate_against_baseline(baseline, candidate)
    n = len(baseline)
    if n == 0:
        return FoldResult(label, 0, None, None, None, None, 0, len(unmatched_candidate))
    mean_baseline = mean(t.r_multiple for t in baseline)
    mean_candidate = mean_baseline + mean(diffs)  # == mean des R candidate (matché ou 0), par construction
    diff = mean(diffs)
    s_diff = stdev(diffs) if n >= 2 else None
    return FoldResult(label, n, mean_baseline, mean_candidate, diff, s_diff,
                      len(unmatched_baseline), len(unmatched_candidate))


@dataclass(frozen=True)
class WalkForwardVerdict:
    hypothesis: str
    diff_a: Optional[float]
    diff_b: Optional[float]
    diff_pooled: Optional[float]
    lower_bound: Optional[float]
    mde: Optional[float]
    n_test_pooled: int
    fidelity_reliable: bool
    status: str
    reason: str


def decide_walk_forward(
    hypothesis: str, fold_a: FoldResult, fold_b: FoldResult, lower_bound: Optional[float],
    expected_effect: float, fidelity_reliable: bool, n_min_test: int = N_MIN_TRAIN,
) -> WalkForwardVerdict:
    """§5, règle de décision littérale : validée / indémontrable / non
    validée. `lower_bound` = borne basse bootstrap (calculée par
    l'appelant avec `one_sample_block_bootstrap_lower_bound` sur les
    diffs RÉELS horodatés des deux folds poolés — jamais recalculée ici).
    `expected_effect` = brut_min repris de `docs/EVOLUTIONS_CANDIDATES.md`
    (05/10), jamais recalculé."""
    n_pooled = fold_a.n_test + fold_b.n_test
    if fold_a.diff is None or fold_b.diff is None or n_pooled < 2:
        return WalkForwardVerdict(
            hypothesis, fold_a.diff, fold_b.diff, None, lower_bound, None, n_pooled, fidelity_reliable,
            "non validée", "au moins un fold de test est vide — aucune différence calculable",
        )

    # Écart-type poolé (moyenne pondérée des variances par fold — approche
    # standard, jamais reconstruite après coup à partir des seules moyennes).
    s_a, s_b = fold_a.s_diff or 0.0, fold_b.s_diff or 0.0
    pooled_var = (
        ((fold_a.n_test - 1) * s_a ** 2 + (fold_b.n_test - 1) * s_b ** 2) / (n_pooled - 2)
        if n_pooled > 2 else max(s_a, s_b) ** 2
    )
    mde = compute_mde(pooled_var ** 0.5, n_pooled)
    diff_pooled = (fold_a.diff * fold_a.n_test + fold_b.diff * fold_b.n_test) / n_pooled
    both_positive = fold_a.diff > 0 and fold_b.diff > 0

    if both_positive and lower_bound is not None and lower_bound > 0 and n_pooled >= n_min_test and fidelity_reliable:
        status, reason = "validée", "D(a)>0, D(b)>0, borne basse corrigée (m=4) > 0, n≥200, fidélité fiable"
    elif mde is not None and mde > expected_effect:
        status, reason = "indémontrable sur cette fenêtre", f"MDE ({mde:.3f} R) > effet attendu ({expected_effect:.3f} R)"
    else:
        status, reason = "non validée", "D(a)>0 et D(b)>0 non réunis, ou borne basse <= 0, ou n insuffisant, ou fidélité non fiable"

    return WalkForwardVerdict(hypothesis, fold_a.diff, fold_b.diff, diff_pooled, lower_bound, mde, n_pooled,
                              fidelity_reliable, status, reason)
