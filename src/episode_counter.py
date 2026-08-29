"""
episode_counter.py — Règle commune de la Mesure B, point 11 (29/08/2026,
voir docs/DECISIONS.md ; règle proposée le 28/08/2026 dans
docs/HYPOTHESES.md, jamais codée jusqu'ici). Le live peut produire
PLUSIEURS lignes `trades` pour le même épisode de signal (péremption
normale à 15 minutes, ou échec de placement qui libère le garde-fou
anti-doublon dès le cycle suivant, point 10) — comparer des lignes
`trades` brutes entre live et backtest (qui produit toujours exactement
une ligne par épisode) est donc invalide. Ce module implémente la
règle : compter des ÉPISODES, jamais des lignes brutes.

**Approximation assumée, documentée plutôt que cachée** : la définition
textuelle du 28/08/2026 regroupe par proximité entre `ouvert_at` d'une
tentative et la RÉSOLUTION (`annule`/`ferme`) de la précédente. Aucun
horodatage de résolution n'est capturé pour un trade `statut='annule'`
(seul `ouvert_at` existe pour ces lignes — vérifié dans `src/db.py`
avant d'écrire ce module). Ce module regroupe donc par proximité entre
`ouvert_at` CONSÉCUTIFS au sein d'un même (actif, source, direction) —
une approximation légèrement plus permissive que la définition
textuelle (elle ignore le temps par lequel la tentative précédente a pu
rester `en_attente` avant résolution), mais calculable avec les données
réellement en base aujourd'hui. Capturer un horodatage de résolution
explicite améliorerait la précision — hors périmètre de ce chantier
(pas demandé, pas nécessaire pour rendre les comptages actuels
utilisables).

Calcul pur (`count_episodes`) : 100% couvert. Orchestration
(`aggregate_episode_counts`) : lecture DB seule.
"""

from datetime import datetime
from typing import Dict, List, Tuple

from src.db import connection_scope


def count_episodes(ouvert_ats: List[str], max_gap_seconds: float) -> int:
    """`ouvert_ats` : horodatages ISO d'un même (actif, source,
    direction), PAS nécessairement triés (triés ici). Deux tentatives
    consécutives appartiennent au même épisode si l'écart entre leurs
    `ouvert_at` est <= `max_gap_seconds` (voir approximation documentée
    ci-dessus) — sinon, nouvel épisode. Liste vide -> 0 épisode."""
    if not ouvert_ats:
        return 0
    timestamps = sorted(datetime.fromisoformat(ts) for ts in ouvert_ats)
    episodes = 1
    for previous, current in zip(timestamps, timestamps[1:]):
        gap = (current - previous).total_seconds()
        if gap > max_gap_seconds:
            episodes += 1
    return episodes


def aggregate_episode_counts(db_path: str, max_gap_seconds: float) -> List[Dict[str, object]]:
    """Une ligne par (source, actif, direction) : `n_lignes_brutes`
    (compte brut, ce qu'une comparaison naïve utiliserait à tort) vs
    `n_episodes` (le compte valide pour toute comparaison live/backtest,
    voir docstring du module)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute("SELECT source, actif, direction, ouvert_at FROM trades").fetchall()

    groups: Dict[Tuple[str, str, str], List[str]] = {}
    for row in rows:
        key = (row["source"], row["actif"], row["direction"])
        groups.setdefault(key, []).append(row["ouvert_at"])

    return [
        {
            "source": source, "actif": actif, "direction": direction,
            "n_lignes_brutes": len(ouvert_ats),
            "n_episodes": count_episodes(ouvert_ats, max_gap_seconds),
        }
        for (source, actif, direction), ouvert_ats in sorted(groups.items())
    ]
