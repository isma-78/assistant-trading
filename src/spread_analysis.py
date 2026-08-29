"""
spread_analysis.py — Point 6 (29/08/2026, voir docs/DECISIONS.md) :
mesure du spread réel par (actif, heure UTC) depuis `market_snapshots`
(déjà alimentée pour CHAQUE signal évalué depuis le 24/08/2026, aucun
appel réseau ici) + distribution horaire des déclenchements par
hypothèse, pour croiser les deux SANS RIEN MODIFIER tant que ce
croisement n'a pas été rapporté (consigne explicite : les mesures a/b
précèdent tout changement de modèle de coûts).

Registre ATTRIBUTION (déterministe, par observation), pas STRATÉGIE —
même distinction que `causal_decomposition.py` : ce module ne conclut
JAMAIS qu'une hypothèse doit changer d'heure de déclenchement, il rend
seulement visible si ses déclenchements se concentrent sur des heures
chères (auquel cas c'est une fuite d'ingénierie à investiguer, point 4 —
jamais un verdict de stratégie).

Deux couches, même convention que le reste du projet :
- Calcul pur (`utc_hour_from_timestamp`) : 100% couvert.
- Orchestration I/O (`hourly_spread_by_asset`,
  `hourly_trigger_distribution_by_source_asset`) : lecture DB seule,
  aucune écriture, même régime que `causal_decomposition.aggregate_by_*`.
"""

from typing import Dict, List, Tuple

from src.db import connection_scope


def utc_hour_from_timestamp(timestamp: str) -> int:
    """Heure UTC (0-23) extraite par position de caractères, PAS par
    parsing complet — fonctionne indifféremment sur les deux formats
    ISO réellement présents en base (`...T14:00:00Z` et
    `...T14:00:00.123456+00:00`, voir `causal_decomposition.py`,
    bug du 29/08/2026) puisque le préfixe date+heure est identique
    caractère pour caractère dans les deux cas jusqu'à la position 13.
    Lève ValueError si le résultat n'est pas dans 0-23 (donnée corrompue,
    jamais un bucket fourre-tout silencieux — même garde-fou que
    `causal_decomposition.session_bucket_from_ouvert_at`)."""
    hour = int(timestamp[11:13])
    if not 0 <= hour <= 23:
        raise ValueError(f"heure hors plage 0-23 extraite de {timestamp!r} : {hour}")
    return hour


def hourly_spread_by_asset(db_path: str) -> List[Dict[str, object]]:
    """Mesure (a) du point 6 : spread réel moyen par (actif, heure UTC),
    depuis `market_snapshots` (une ligne par signal ÉVALUÉ, approuvé ou
    non — capture ajoutée le 24/08/2026, aucun appel réseau). Exclut les
    lignes sans `spread` connu (jamais traité comme 0)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT s.actif AS actif, m.captured_at AS captured_at, m.spread AS spread "
            "FROM market_snapshots m JOIN signals s ON s.id = m.signal_id "
            "WHERE m.spread IS NOT NULL"
        ).fetchall()

    groups: Dict[Tuple[str, int], List[float]] = {}
    for row in rows:
        hour = utc_hour_from_timestamp(row["captured_at"])
        groups.setdefault((row["actif"], hour), []).append(row["spread"])

    return [
        {"actif": actif, "heure_utc": hour, "n": len(spreads), "spread_moyen": sum(spreads) / len(spreads)}
        for (actif, hour), spreads in sorted(groups.items())
    ]


def hourly_trigger_distribution_by_source_asset(db_path: str) -> List[Dict[str, object]]:
    """Mesure (b) du point 6 : distribution horaire des déclenchements
    (signaux GÉNÉRÉS, approuvés ou non — la question est "quand
    l'hypothèse déclenche", pas "quand elle finit par trader") par
    (source, actif, heure UTC), depuis `signals.created_at`."""
    with connection_scope(db_path) as conn:
        rows = conn.execute("SELECT source, actif, created_at FROM signals").fetchall()

    groups: Dict[Tuple[str, str, int], int] = {}
    for row in rows:
        hour = utc_hour_from_timestamp(row["created_at"])
        key = (row["source"], row["actif"], hour)
        groups[key] = groups.get(key, 0) + 1

    return [
        {"source": source, "actif": actif, "heure_utc": hour, "n": n}
        for (source, actif, hour), n in sorted(groups.items())
    ]


def cross_triggers_with_spread(
    triggers: List[Dict[str, object]], spread_curve: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Annote chaque ligne de `triggers` avec le spread moyen connu pour
    son (actif, heure_utc) — `None` si cette combinaison n'a jamais été
    observée dans `market_snapshots` (jamais une valeur imputée). Calcul
    pur, aucune I/O : les deux listes sont déjà en mémoire."""
    spread_by_key = {(row["actif"], row["heure_utc"]): row["spread_moyen"] for row in spread_curve}
    return [
        {**trigger, "spread_moyen_a_cette_heure": spread_by_key.get((trigger["actif"], trigger["heure_utc"]))}
        for trigger in triggers
    ]
