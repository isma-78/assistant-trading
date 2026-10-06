"""
shadow_weekly_report.py — Rapport hebdomadaire du suivi shadow des 4
candidates V2 (07/10/2026, étape 4 du mandat). Calcul pur + lecture DB
en lecture seule — aucune écriture ailleurs que le fichier de rapport et
l'envoi Telegram (orchestrés par `scripts/rapport_hebdo_shadow.py`).
Aucune action sur un exécuteur ou une configuration, jamais.

Réutilise `src.evolution_v2_test`/`src.shadow_milestone`/
`src.simulator_fidelity` SANS LES MODIFIER.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from src.evolution_v2_test import FORWARD_BONFERRONI_M, TradeRef, decide_forward_verdict
from src.shadow_milestone import MILESTONES, MIN_WEEKS_SINCE_T0, weeks_since
from src.simulator_fidelity import PortfolioTrade, apply_cluster_cap

PROVISIONAL_RISK_EUR_SHADOW = 20.2  # même ordre que executor.open_signal (taux boosté x enveloppe), voir docs A5/A8


@dataclass(frozen=True)
class WeeklyHypothesisReport:
    source: str
    started_at: Optional[str]
    weeks_elapsed: Optional[float]
    n_shadow: int
    n_live_baseline: int
    mean_shadow: Optional[float]
    mean_live: Optional[float]
    diff: Optional[float]
    lower_bound: Optional[float]
    mde: Optional[float]
    status: str
    milestones_reached: List[int]
    next_milestone: Optional[int]


def build_hypothesis_report(
    source: str, started_at: Optional[str], shadow_trades: Sequence[TradeRef], live_trades: Sequence[TradeRef],
    expected_effect: float, now: datetime,
) -> WeeklyHypothesisReport:
    """Un rapport par candidate — réutilise `decide_forward_verdict` tel
    quel (même m=44, même appariement)."""
    if started_at is None:
        return WeeklyHypothesisReport(source, None, None, len(shadow_trades), len(live_trades), None, None, None,
                                      None, None, "aucune époque shadow écrite", [], MILESTONES[0])
    weeks = weeks_since(started_at, now)
    verdict = decide_forward_verdict(source, live_trades, shadow_trades, expected_effect)
    mean_shadow = sum(t.r_multiple for t in shadow_trades) / len(shadow_trades) if shadow_trades else None
    mean_live = sum(t.r_multiple for t in live_trades) / len(live_trades) if live_trades else None
    reached = [m for m in MILESTONES if len(shadow_trades) >= m]
    next_m = next((m for m in MILESTONES if m not in reached), None)
    return WeeklyHypothesisReport(
        source, started_at, weeks, len(shadow_trades), len(live_trades), mean_shadow, mean_live,
        verdict.diff, verdict.lower_bound, verdict.mde, verdict.status, reached, next_m,
    )


def cluster_rejection_rate(pooled_trades: Sequence[PortfolioTrade], provisional_risk_eur: float = PROVISIONAL_RISK_EUR_SHADOW) -> Optional[float]:
    """Part des trades shadow (portefeuille poolé des 4 candidates) qui
    auraient été bloqués par le plafond de cluster (`src.simulator_
    fidelity.apply_cluster_cap`, inchangé) s'ils avaient dû partager le
    même plafond que les hypothèses `_v2`. `None` si aucun trade.

    `pooled_trades` : construits par l'appelant depuis `shadow_trades`
    (lignes DB, `ouvert_at`/`ferme_at` réels) — jamais depuis `TradeRef`
    seul, qui ne porte aucun horodatage de clôture (une position dont la
    fenêtre d'ouverture est inconnue ne peut jamais être comptée comme
    « engagée » pour une autre, voir `apply_cluster_cap`)."""
    if not pooled_trades:
        return None
    kept, blocked = apply_cluster_cap(pooled_trades, provisional_risk_eur)
    return len(blocked) / len(pooled_trades)


def format_weekly_markdown(reports: Sequence[WeeklyHypothesisReport], date_iso: str, cluster_rate: Optional[float]) -> str:
    lines = [f"# Suivi shadow hebdomadaire — {date_iso}", "",
             "Lecture seule, aucune action automatique. Voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md "
             "§6-7 pour les règles de jalon/verdict (m=44).", ""]
    for r in reports:
        lines.append(f"## {r.source}")
        if r.started_at is None:
            lines.append("Aucune époque shadow écrite — suivi non démarré.\n")
            continue
        lines += [
            f"- T0 = {r.started_at} ({r.weeks_elapsed:.1f} semaines écoulées, seuil {MIN_WEEKS_SINCE_T0})",
            f"- n shadow = {r.n_shadow} ; n live (baseline v2) = {r.n_live_baseline}",
            f"- Espérance shadow = {r.mean_shadow} ; espérance live = {r.mean_live}",
            f"- Différence appariée = {r.diff} ; borne basse (m={FORWARD_BONFERRONI_M}) = {r.lower_bound} ; MDE = {r.mde}",
            f"- Jalons atteints : {r.milestones_reached or 'aucun'} ; prochain jalon : {r.next_milestone}",
            f"- Statut forward : {r.status}", "",
        ]
    if cluster_rate is not None:
        lines.append(f"## Plafond de cluster (portefeuille shadow poolé)\nTaux de rejet estimé : {cluster_rate:.1%}\n")
    return "\n".join(lines)


def format_weekly_telegram(reports: Sequence[WeeklyHypothesisReport]) -> str:
    parts = ["📅 Suivi shadow hebdomadaire — aucune action automatique."]
    for r in reports:
        if r.started_at is None:
            parts.append(f"{r.source} : non démarré")
            continue
        parts.append(f"{r.source} : n={r.n_shadow}, diff={r.diff}, statut={r.status}, "
                     f"prochain jalon={r.next_milestone} ({r.weeks_elapsed:.1f} sem.)")
    return "\n".join(parts)
