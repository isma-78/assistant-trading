"""
go_nogo.py — Verrou de passage en exécution réelle. MODULE CRITIQUE.

Invariant #4 : le passage en mode réel est verrouillé par code, jamais par
la seule discipline personnelle. Ce module est la porte de décision ;
c'est le redéploiement avec CAPITAL_ENVIRONMENT=live (invariant #6) qui
reste le seul mécanisme physique de bascule — jamais une variable modifiée
à chaud.

evaluate_go_nogo() ne fait que dire si les conditions sont réunies. Il ne
modifie jamais lui-même la configuration.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GoNoGoStatus:
    allowed: bool
    reason: str


def evaluate_go_nogo(
    configured_environment: str,
    demo_trades_count: int,
    min_demo_trades_required: int,
    risk_engine_tests_passing: bool,
) -> GoNoGoStatus:
    """
    Toutes les conditions doivent être vraies simultanément :
    - configured_environment == "live" (donc déjà redéployé en ce sens)
    - suite de tests risk_engine verte à 100%
    - historique démo suffisant (seuil "Porte A", cf CDC)
    """
    if configured_environment != "live":
        return GoNoGoStatus(
            allowed=False,
            reason="CAPITAL_ENVIRONMENT n'est pas 'live' (redéploiement requis, jamais de bascule à chaud)",
        )

    if not risk_engine_tests_passing:
        return GoNoGoStatus(
            allowed=False,
            reason="Suite de tests risk_engine non verte à 100% : passage en réel bloqué",
        )

    if demo_trades_count < min_demo_trades_required:
        return GoNoGoStatus(
            allowed=False,
            reason=f"Historique démo insuffisant : {demo_trades_count}/{min_demo_trades_required} trades",
        )

    return GoNoGoStatus(allowed=True, reason="Conditions Go/No-Go réunies")
