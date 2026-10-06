"""
shadow_milestone.py — Jalons, alertes et verdict forward des 4 candidates
V2 en shadow (06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §6-7).
Calcul pur (lecture de lignes déjà extraites de la base par l'appelant),
aucune I/O ici — `scripts/shadow_milestone_alerts.py` orchestre les accès
DB/Telegram.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from src.evolution_v2_test import ForwardVerdict, TradeRef, decide_forward_verdict
from src.verdict_counter import reached_milestones  # réutilisé tel quel (même sémantique de jalon)

MILESTONES = (30, 40, 53)
MIN_WEEKS_SINCE_T0 = 8


def weeks_since(started_at: str, now: datetime) -> float:
    started = datetime.fromisoformat(started_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (now - started).total_seconds() / (7 * 86400)


def eligible_milestones(n: int, weeks_elapsed: float, milestones: Sequence[int] = MILESTONES) -> List[int]:
    """§6 : un jalon n'est ATTEINT (pour l'alerte ET le verdict) que si
    n >= jalon ET au moins `MIN_WEEKS_SINCE_T0` semaines depuis T0 — le
    plus tardif des deux conditions, jamais l'une sans l'autre."""
    if weeks_elapsed < MIN_WEEKS_SINCE_T0:
        return []
    return [m for m in milestones if n >= m]


def pending_alerts(previous_eligible: List[int], current_eligible: List[int]) -> List[int]:
    """Jalons nouvellement atteints (éligibles maintenant, pas encore
    annoncés) — même idée que `verdict_counter.reached_milestones`,
    appliquée à la liste déjà filtrée par `eligible_milestones`."""
    return [m for m in current_eligible if m not in previous_eligible]


@dataclass(frozen=True)
class ShadowStatus:
    source: str
    n_closed: int
    mean_r: Optional[float]
    weeks_elapsed: float
    eligible: List[int]
    new_alerts: List[int]


def compute_status(
    source: str, n_closed: int, mean_r: Optional[float], started_at: str, now: datetime, previously_alerted: List[int],
) -> ShadowStatus:
    weeks = weeks_since(started_at, now)
    eligible = eligible_milestones(n_closed, weeks)
    return ShadowStatus(source, n_closed, mean_r, weeks, eligible, pending_alerts(previously_alerted, eligible))


def format_milestone_message(status: ShadowStatus, milestone: int, mde: Optional[float]) -> str:
    mean = f"{status.mean_r:+.3f} R" if status.mean_r is not None else "n/a"
    mde_txt = f"{mde:.3f} R" if mde is not None else "n/a"
    return (
        f"🔬 Shadow — {status.source} : jalon {milestone} atteint ({status.n_closed} signaux shadow réconciliés, "
        f"{status.weeks_elapsed:.1f} semaines depuis T0). Espérance shadow : {mean}. MDE (m=44) : {mde_txt}. "
        "Aucune action automatique : la promotion reste une décision d'Ismaël (walk-forward validé ET forward confirmé requis)."
    )


def format_verdict_message(status: ShadowStatus, verdict: ForwardVerdict) -> str:
    return (
        f"📈 Shadow — {status.source} : verdict forward = {verdict.status} ({verdict.reason}). "
        f"n={verdict.n}, diff={verdict.diff}, borne basse={verdict.lower_bound}, MDE={verdict.mde}."
    )
