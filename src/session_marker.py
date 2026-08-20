"""
session_marker.py — Marquage de la session de marché à l'ouverture d'un
trade (Station X et Flux B, 20/08/2026, voir docs/DECISIONS.md).

Collecte UNIQUEMENT (demande explicite d'Ismaël, même patron que
trade_features_store.record_align_matinale) : n'influence aucune décision
d'entrée, de sizing ni de sortie. Sert uniquement à permettre plus tard
une analyse de la performance par session, si utile. Ce n'est PAS une
variable du §3.8 (invariant #10) — elle n'entre dans aucune décision,
contrairement aux 5 variables dont le budget est explicitement suivi
dans docs/HYPOTHESES.md ; pas soumise à la règle des 10 trades/variable.

Pure, aucun accès DB — la valeur calculée est écrite directement par
l'appelant (executor.open_signal) dans trades.session_marche.
"""

# Convention documentée le 20/08/2026 (docs/DECISIONS.md) : trois plages
# horaires standard de trading, en UTC, telles que fournies par Ismaël —
# Asie 00h-08h, Europe 07h-16h, US 13h-21h. Les chevauchements (07h-08h
# Asie/Europe, 13h-16h Europe/US) sont résolus par priorité US > Europe >
# Asie : la session qui démarre le plus tard dans un chevauchement est
# conventionnellement celle où la liquidité bascule (l'arrivée de New York
# domine l'après-midi européenne, l'arrivée de Londres domine la fin de
# nuit asiatique) — choix de convention, pas une donnée mesurée.
_US_START, _US_END = 13, 21
_EUROPE_START, _EUROPE_END = 7, 16
_ASIE_START, _ASIE_END = 0, 8


def compute_market_session(hour_utc: int) -> str:
    """Session de marché ("asie" | "europe" | "us" | "hors_session") pour
    l'heure UTC donnée (0-23 inclus). "hors_session" couvre le creux de
    liquidité mondial ~21h-24h UTC, hors des trois plages ci-dessus.
    Lève ValueError sur une heure hors [0, 23] plutôt que de deviner."""
    if not (0 <= hour_utc <= 23):
        raise ValueError(f"hour_utc doit être entre 0 et 23 inclus, obtenu {hour_utc}")
    if _US_START <= hour_utc < _US_END:
        return "us"
    if _EUROPE_START <= hour_utc < _EUROPE_END:
        return "europe"
    if _ASIE_START <= hour_utc < _ASIE_END:
        return "asie"
    return "hors_session"
