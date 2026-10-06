"""
fidelity_continuous.py — Remesure mensuelle de la fidélité H1-H4
(07/10/2026, étape 4 du mandat). Calcul pur (agrégation/décision) —
l'orchestration (rejeu du simulateur, lecture DB, Telegram) vit dans
`scripts/mesure_fidelite_continue.py`. Réutilise
`src.fidelity_measurement` SANS LE MODIFIER.
"""

from dataclasses import dataclass
from typing import Optional

from src.fidelity_measurement import FidelityMetrics, fidelity_status

MIN_N_ESTABLISHED = 20


@dataclass(frozen=True)
class FidelityUpdate:
    hypothesis: str
    previous_n_paired: int
    current_n_paired: int
    previous_status: str
    current_status: str
    just_crossed_20: bool


def compute_update(hypothesis: str, previous_n_paired: int, previous_status: str, metrics: FidelityMetrics) -> FidelityUpdate:
    """Compare le nouvel état mesuré au précédent (mémorisé par
    l'appelant, `system_state`) — alerte seulement sur le passage de
    n < 20 à n >= 20 POUR LA PREMIÈRE FOIS, jamais à chaque mesure."""
    current_status = fidelity_status(metrics)
    just_crossed = previous_n_paired < MIN_N_ESTABLISHED <= metrics.n_paired
    return FidelityUpdate(hypothesis, previous_n_paired, metrics.n_paired, previous_status, current_status, just_crossed)


def format_first_n20_message(update: FidelityUpdate, metrics: FidelityMetrics) -> str:
    return (
        f"📏 Fidélité {update.hypothesis} : n=20 paires atteint pour la première fois "
        f"(était {update.previous_n_paired}). Écart absolu moyen = {metrics.mean_abs_gap}, "
        f"biais signé = {metrics.mean_signed_bias} -> statut : {update.current_status}. "
        "Aucune action automatique."
    )


def format_monthly_summary_line(update: FidelityUpdate, metrics: FidelityMetrics) -> str:
    return (
        f"{update.hypothesis} : n={metrics.n_paired} (était {update.previous_n_paired}) "
        f"écart={metrics.mean_abs_gap} biais={metrics.mean_signed_bias} -> {update.current_status}"
    )
