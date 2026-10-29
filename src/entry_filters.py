"""
entry_filters.py — filtres d'entrée déterministes AVANT l'envoi d'un ordre
(analyse du 29/10/2026, docs/corrections_hypotheses_2026-10-29.md §4).

Aucun LLM, aucun accès broker : deux fonctions pures. Aucune ne modifie un
stop ni une taille ; elles ne font que REFUSER une entrée (fail-safe,
invariant #7 : donnée manquante ou invalide => refus).
"""

from typing import Optional

# Coût d'un aller-retour (spread) rapporté à la distance du stop = coût en R
# avant glissement et financement. Au-delà de 0,10 R, le coût consomme une
# espérance typique de 0,1-0,2 R (docs/adequation_paires_2026-10-29.md §1).
MAX_SPREAD_TO_STOP_RATIO = 0.10


def spread_to_stop_ratio(bid: Optional[float], ask: Optional[float], entry: float, stop: float) -> Optional[float]:
    """spread / |entrée - stop|, ou None si une donnée est absente/invalide."""
    if bid is None or ask is None or ask < bid:
        return None
    distance = abs(entry - stop)
    if distance <= 0:
        return None
    return (ask - bid) / distance


def spread_filter_rejection(
    bid: Optional[float], ask: Optional[float], entry: float, stop: float,
    max_ratio: float = MAX_SPREAD_TO_STOP_RATIO,
) -> Optional[str]:
    """Message de rejet si le coût d'entrée dépasse `max_ratio` R, ou si le
    ratio est incalculable (fail-safe) ; None si l'entrée est acceptable."""
    ratio = spread_to_stop_ratio(bid, ask, entry, stop)
    if ratio is None:
        return "Rejeté : spread ou distance de stop incalculable (fail-safe)"
    if ratio > max_ratio:
        return (
            f"Rejeté : spread/stop = {ratio:.3f} R > {max_ratio} R "
            f"(spread={round(ask - bid, 10)}, distance stop={round(abs(entry - stop), 10)})"
        )
    return None


def is_limit_level_marketable(direction: str, level: float, bid: Optional[float], ask: Optional[float]) -> bool:
    """True si un ordre LIMITE à `level` serait exécutable immédiatement (le
    broker le refuse alors avec `error.validation.limit.price`). Même règle
    que `executor.describe_limit_refusal` : achat marketable si level >= ask,
    vente marketable si bid >= level. Prix absents => False (on laisse le
    broker trancher, comportement actuel inchangé)."""
    if bid is None or ask is None:
        return False
    if direction == "long":
        return level - ask >= 0
    if direction == "short":
        return bid - level >= 0
    return False
