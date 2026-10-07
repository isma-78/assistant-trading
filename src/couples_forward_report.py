"""
couples_forward_report.py — Rapport hebdomadaire forward de TOUS les
couples (hypothèse _v2 x actif de la liste blanche), Partie 4 du
08/10/2026 (`docs/COUPLES_V2_08-10.md` étape 5). Calcul pur, aucune
I/O — `scripts/rapport_couples_forward.py` orchestre les accès DB.

Par couple : n signaux shadow et réels (closés, réconciliés), espérance
nette de chaque côté avec IC bootstrap par blocs calendaires (semaine
ISO), avancement vers n=30/50/100 (shadow+réel combinés), taux de rejet
par plafond de cluster (mesuré sur le portefeuille shadow poolé de tous
les couples, `src.simulator_fidelity.apply_cluster_cap`, inchangé).
Aucune action automatique, aucun verdict, aucune sélection.
"""

import random
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from src.evolution_v2_test import TradeRef
from src.simulator_fidelity import PortfolioTrade, apply_cluster_cap

MILESTONES_COUPLES = (30, 50, 100)
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 20261008
CI_QUANTILES = (0.025, 0.975)


def _iso_week(entry_time_utc: str) -> Tuple[int, int]:
    year, week, _ = date.fromisoformat(entry_time_utc[:10]).isocalendar()
    return year, week


def bootstrap_mean_ci(
    trades: Sequence[TradeRef], quantiles: Tuple[float, float] = CI_QUANTILES,
    reps: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED,
) -> Optional[Tuple[float, float]]:
    """IC bootstrap par blocs calendaires (semaine ISO de l'horodatage
    d'entrée), sur les `r_multiple` de `trades`. `None` si vide — jamais
    un intervalle inventé faute de donnée."""
    if not trades:
        return None
    blocks: Dict[Tuple[int, int], List[float]] = {}
    for t in trades:
        blocks.setdefault(_iso_week(t.entry_time_utc), []).append(t.r_multiple)
    keys = list(blocks)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        sample = [v for _ in keys for v in blocks[keys[rng.randrange(len(keys))]]]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo_idx = min(len(means) - 1, int(quantiles[0] * len(means)))
    hi_idx = min(len(means) - 1, int(quantiles[1] * len(means)))
    return means[lo_idx], means[hi_idx]


@dataclass(frozen=True)
class CoupleReport:
    hypothesis: str
    asset: str
    n_shadow: int
    n_live: int
    mean_shadow: Optional[float]
    mean_live: Optional[float]
    ci_shadow: Optional[Tuple[float, float]]
    ci_live: Optional[Tuple[float, float]]
    milestones_reached: List[int]
    next_milestone: Optional[int]
    cluster_rejection_rate: Optional[float]


def build_couple_report(
    hypothesis: str, asset: str, shadow_trades: Sequence[TradeRef], live_trades: Sequence[TradeRef],
    cluster_rejection_rate: Optional[float] = None, milestones: Sequence[int] = MILESTONES_COUPLES,
) -> CoupleReport:
    n_shadow, n_live = len(shadow_trades), len(live_trades)
    mean_shadow = sum(t.r_multiple for t in shadow_trades) / n_shadow if n_shadow else None
    mean_live = sum(t.r_multiple for t in live_trades) / n_live if n_live else None
    n_total = n_shadow + n_live  # avancement vers le jalon : shadow + réel combinés (même couple)
    reached = [m for m in milestones if n_total >= m]
    next_m = next((m for m in milestones if m not in reached), None)
    return CoupleReport(
        hypothesis, asset, n_shadow, n_live, mean_shadow, mean_live,
        bootstrap_mean_ci(shadow_trades), bootstrap_mean_ci(live_trades),
        reached, next_m, cluster_rejection_rate,
    )


def cluster_rejection_rates(
    pooled_trades: Sequence[PortfolioTrade], provisional_risk_eur: float,
) -> Dict[Tuple[str, str], float]:
    """Taux de rejet par plafond de cluster, PAR COUPLE (source, actif)
    — jamais un seul taux global (chaque couple peut être affecté
    différemment selon son cluster de corrélation). `{}` si vide."""
    if not pooled_trades:
        return {}
    _, blocked = apply_cluster_cap(pooled_trades, provisional_risk_eur)
    totals: Dict[Tuple[str, str], int] = {}
    blocked_counts: Dict[Tuple[str, str], int] = {}
    for t in pooled_trades:
        key = (t.source, t.asset)
        totals[key] = totals.get(key, 0) + 1
    for t in blocked:
        key = (t.source, t.asset)
        blocked_counts[key] = blocked_counts.get(key, 0) + 1
    return {key: blocked_counts.get(key, 0) / total for key, total in totals.items()}


def format_markdown(reports: Sequence[CoupleReport], date_iso: str) -> str:
    lines = [f"# Suivi forward des couples — {date_iso}", "",
             "Lecture seule, aucune action automatique, aucun verdict, aucune sélection.", ""]
    for r in reports:
        lines.append(f"## {r.hypothesis} x {r.asset}")
        lines += [
            f"- n shadow = {r.n_shadow} ; n réel = {r.n_live}",
            f"- Espérance shadow = {r.mean_shadow} (IC95% bootstrap par blocs calendaires : {r.ci_shadow})",
            f"- Espérance réelle = {r.mean_live} (IC95% bootstrap par blocs calendaires : {r.ci_live})",
            f"- Jalons atteints (n shadow+réel, 30/50/100) : {r.milestones_reached or 'aucun'} ; "
            f"prochain : {r.next_milestone}",
            f"- Taux de rejet par plafond de cluster : {r.cluster_rejection_rate}",
            "",
        ]
    return "\n".join(lines)
