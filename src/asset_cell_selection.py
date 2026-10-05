"""
asset_cell_selection.py — Implémentation littérale du protocole
pré-enregistré `docs/PROTOCOLE_EVOLUTION_05-10.md` (commit 415dbb5, écrit
avant tout calcul) : classement des cellules (hypothèse × actif) et test
walk-forward de la procédure. Calcul pur, aucune I/O, aucun appel broker.

Rien ici ne décide d'une restriction live : le module produit des statuts
et des statistiques ; l'application (§5 du protocole) reste une décision
humaine au redéploiement.
"""

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

N_MIN = 100
SHRINK_K = 100
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 20261005

RETAINED = "retenue"
NOT_RETAINED = "non retenue"
NOT_CLASSIFIABLE = "non classable"


@dataclass(frozen=True)
class CellTrade:
    hypothesis: str
    asset: str
    entry_time: str  # ISO UTC
    r: float


@dataclass(frozen=True)
class CellResult:
    asset: str
    n: int
    mean_r: Optional[float]
    contracted: Optional[float]
    mean_half1: Optional[float]
    mean_half2: Optional[float]
    status: str
    reason: str


def _in(t: CellTrade, start: str, end: str) -> bool:
    return start <= t.entry_time < end


def _mean(values: Sequence[float]) -> Optional[float]:
    return mean(values) if values else None


def classify_cells(
    trades: Iterable[CellTrade], assets: Sequence[str], period: Tuple[str, str],
    halves: Tuple[Tuple[str, str], Tuple[str, str]], excluded_assets: Sequence[str] = (),
    n_min: int = N_MIN, shrink_k: int = SHRINK_K,
) -> Dict[str, CellResult]:
    """§3 du protocole. `trades` : trades d'UNE hypothèse. Bornes [début,
    fin[ en ISO. Retenue = contractée > 0 ET espérance brute > 0 sur chaque
    moitié (lecture prudente « identique ET positif »)."""
    in_period = [t for t in trades if _in(t, *period)]
    hyp_mean = _mean([t.r for t in in_period]) or 0.0
    results = {}
    for asset in assets:
        cell = [t for t in in_period if t.asset == asset]
        n = len(cell)
        m = _mean([t.r for t in cell])
        h1 = _mean([t.r for t in cell if _in(t, *halves[0])])
        h2 = _mean([t.r for t in cell if _in(t, *halves[1])])
        contracted = None
        if m is not None:
            w = n / (n + shrink_k)
            contracted = w * m + (1 - w) * hyp_mean
        if asset in excluded_assets:
            status, reason = NOT_CLASSIFIABLE, "exclu du pré-enregistrement (décision 4)"
        elif n < n_min:
            status, reason = NOT_CLASSIFIABLE, f"n={n} < {n_min}" if n else "aucun trade (historique absent ou aucun signal)"
        elif contracted > 0 and h1 is not None and h2 is not None and h1 > 0 and h2 > 0:
            status, reason = RETAINED, "contractée > 0 et positive sur les deux moitiés"
        else:
            status, reason = NOT_RETAINED, "contractée ≤ 0 ou signe instable entre moitiés"
        results[asset] = CellResult(asset, n, m, contracted, h1, h2, status, reason)
    return results


def test_group_returns(
    trades: Iterable[CellTrade], statuses: Dict[str, CellResult], test_period: Tuple[str, str],
) -> Tuple[List[CellTrade], List[CellTrade]]:
    """Trades de la période de test, séparés en (retenues, non retenues).
    Les cellules non classables sont exclues des deux groupes."""
    retained, others = [], []
    for t in trades:
        if not _in(t, *test_period):
            continue
        status = statuses[t.asset].status if t.asset in statuses else NOT_CLASSIFIABLE
        if status == RETAINED:
            retained.append(t)
        elif status == NOT_RETAINED:
            others.append(t)
    return retained, others


def group_difference(retained: Sequence[CellTrade], others: Sequence[CellTrade]) -> Optional[float]:
    if not retained or not others:
        return None
    return mean(t.r for t in retained) - mean(t.r for t in others)


def iso_week(entry_time: str) -> Tuple[int, int]:
    year, week, _ = date.fromisoformat(entry_time[:10]).isocalendar()
    return year, week


def block_bootstrap_lower_bound(
    labelled: Sequence[Tuple[CellTrade, bool]], quantile: float,
    reps: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED,
) -> Tuple[Optional[float], int]:
    """Borne basse (quantile unilatéral) de la différence retenues − non
    retenues, bootstrap par blocs calendaires (semaine ISO de l'entrée).
    `labelled` = [(trade, est_retenue)]. Un tirage où un groupe est vide
    est compté comme invalide et ignoré ; leur nombre est renvoyé. None si
    aucun tirage valide."""
    blocks: Dict[Tuple[int, int], List[Tuple[float, bool]]] = defaultdict(list)
    for trade, is_retained in labelled:
        blocks[iso_week(trade.entry_time)].append((trade.r, is_retained))
    keys = sorted(blocks)
    if not keys:
        return None, reps
    rng = random.Random(seed)
    diffs, invalid = [], 0
    for _ in range(reps):
        ret_sum = ret_n = oth_sum = oth_n = 0
        for _ in keys:
            for r, is_retained in blocks[keys[rng.randrange(len(keys))]]:
                if is_retained:
                    ret_sum += r
                    ret_n += 1
                else:
                    oth_sum += r
                    oth_n += 1
        if ret_n == 0 or oth_n == 0:
            invalid += 1
            continue
        diffs.append(ret_sum / ret_n - oth_sum / oth_n)
    if not diffs:
        return None, invalid
    diffs.sort()
    return diffs[min(len(diffs) - 1, int(quantile * len(diffs)))], invalid


@dataclass(frozen=True)
class WalkForwardResult:
    hypothesis: str
    diff_a: Optional[float]
    diff_b: Optional[float]
    pooled_diff: Optional[float]
    lower_bound: Optional[float]
    invalid_resamples: int
    n_retained_test: int
    n_other_test: int
    validated: bool
    reason: str


def walk_forward(
    hypothesis: str, trades: Sequence[CellTrade], assets: Sequence[str], excluded_assets: Sequence[str] = (),
    m_correction: int = 5,
) -> WalkForwardResult:
    """§4 du protocole : folds (a) 2019-2020 → 2021, (b) 2019-2021 → 2022."""
    fold_a = classify_cells(trades, assets, ("2019-01-01", "2021-01-01"),
                            (("2019-01-01", "2020-01-01"), ("2020-01-01", "2021-01-01")), excluded_assets)
    fold_b = classify_cells(trades, assets, ("2019-01-01", "2022-01-01"),
                            (("2019-01-01", "2020-07-01"), ("2020-07-01", "2022-01-01")), excluded_assets)
    ret_a, oth_a = test_group_returns(trades, fold_a, ("2021-01-01", "2022-01-01"))
    ret_b, oth_b = test_group_returns(trades, fold_b, ("2022-01-01", "2023-01-01"))
    diff_a, diff_b = group_difference(ret_a, oth_a), group_difference(ret_b, oth_b)
    labelled = [(t, True) for t in ret_a + ret_b] + [(t, False) for t in oth_a + oth_b]
    pooled = group_difference(ret_a + ret_b, oth_a + oth_b)
    lower, invalid = block_bootstrap_lower_bound(labelled, 0.05 / m_correction)
    if diff_a is None or diff_b is None:
        validated, reason = False, "groupe vide dans au moins un fold (aucune cellule retenue ou aucune non retenue)"
    elif diff_a > 0 and diff_b > 0 and lower is not None and lower > 0:
        validated, reason = True, "D(a) > 0, D(b) > 0 et borne basse corrigée m=5 > 0"
    else:
        validated, reason = False, "condition D(a) > 0, D(b) > 0, borne basse > 0 non remplie"
    return WalkForwardResult(hypothesis, diff_a, diff_b, pooled, lower, invalid,
                             len(ret_a) + len(ret_b), len(oth_a) + len(oth_b), validated, reason)
