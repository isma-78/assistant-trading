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


ORDER_LIMIT = "LIMIT"
ORDER_STOP = "STOP"


def choose_entry_order_type(direction: str, level: float, bid: Optional[float], ask: Optional[float]) -> Optional[str]:
    """Type d'ordre d'attente pour entrer au niveau `level`, décidé par code.
    - niveau côté attente d'un LIMIT (achat sous l'ask, vente au-dessus du
      bid) -> "LIMIT" ;
    - niveau STRICTEMENT au-delà du marché (achat au-dessus de l'ask, vente
      sous le bid) -> "STOP" (buy-stop au-dessus, sell-stop en dessous) ;
    - niveau exactement sur le prix, direction inconnue ou prix absents ->
      None (refus, fail-safe : aucun des deux types n'est valide)."""
    if bid is None or ask is None or direction not in ("long", "short"):
        return None
    if not is_limit_level_marketable(direction, level, bid, ask):
        return ORDER_LIMIT
    strictly_beyond = level > ask if direction == "long" else level < bid
    return ORDER_STOP if strictly_beyond else None


def stop_protection_rejection(direction: str, level: float, stop_distance: Optional[float]) -> Optional[str]:
    """Message de rejet si un ordre STOP d'entrée ne peut pas porter un stop
    de protection valide dès sa création (distance absente, nulle, négative
    ou non finie). Aucun ordre STOP sans stop. Le stop est toujours placé du
    côté perdant : achat -> level - distance, vente -> level + distance."""
    if stop_distance is None or not isinstance(stop_distance, (int, float)) or not (stop_distance > 0)             or stop_distance == float("inf"):
        return "Rejeté : ordre STOP sans stop de protection valide (distance absente ou non positive)"
    stop_price = level - stop_distance if direction == "long" else level + stop_distance
    side_ok = stop_price < level if direction == "long" else stop_price > level
    return None if side_ok else "Rejeté : stop de protection du mauvais côté de l'entrée"
