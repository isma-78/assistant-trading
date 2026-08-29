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

import statistics
from typing import Dict, List, Optional, Set, Tuple

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


# ---------------------------------------------------------------------------
# Règle de blocage horaire (29/08/2026, voir docs/DECISIONS.md) — CORRECTION
# DE COÛT, jamais une variable de stratégie (même statut que la correction
# du slippage d'entrée du 26/08/2026) : elle n'évalue jamais un rendement
# attendu, elle évite un coût mesuré et déterministe. Ne consomme donc
# aucun budget invariant #10, ne brûle aucune fenêtre de test.
#
# CE QUE CE MODULE NE FAIT PAS : il ne revendique JAMAIS d'amélioration
# d'espérance de signal. Bloquer une fenêtre change la population de
# trades ; l'effet sur l'edge est inconnu et ne se mesure que par un test
# a posteriori (jamais anticipé ici). `compute_expensive_hour_cost`
# retourne UNIQUEMENT le coût économisé (arithmétique certaine sur des
# trades déjà survenus), jamais un effet sur le signal.
# ---------------------------------------------------------------------------

# Seuil de matérialité, fixé AVANT tout calcul par actif (mécanique,
# reproductible depuis la courbe mesurée — jamais un choix visuel/
# subjectif par actif) : une heure est "chère" pour un actif si son
# spread moyen est >= 2x la MÉDIANE des 24 heures de ce même actif.
EXPENSIVE_HOUR_RATIO_THRESHOLD = 2.0


def compute_expensive_hours(asset_hourly_rows: List[Dict[str, object]], threshold: float = EXPENSIVE_HOUR_RATIO_THRESHOLD) -> Set[int]:
    """`asset_hourly_rows` : sous-ensemble de `hourly_spread_by_asset`
    déjà filtré sur UN SEUL actif (24 lignes au maximum, une par heure
    observée). Retourne l'ensemble des heures UTC "chères" selon le
    seuil mécanique ci-dessus. Liste vide ou médiane nulle -> ensemble
    vide (jamais un blocage inventé faute de donnée)."""
    if not asset_hourly_rows:
        return set()
    spreads = [row["spread_moyen"] for row in asset_hourly_rows]
    median = statistics.median(spreads)
    if median <= 0:
        return set()
    return {row["heure_utc"] for row in asset_hourly_rows if row["spread_moyen"] >= threshold * median}


def compute_expensive_hours_by_asset(db_path: str, threshold: float = EXPENSIVE_HOUR_RATIO_THRESHOLD) -> Dict[str, Set[int]]:
    """Orchestration : mesure la courbe réelle (`hourly_spread_by_asset`)
    puis dérive les heures chères par actif, mécaniquement — aucun choix
    manuel d'heure, aucune borne "20-22h" imposée d'office à un actif
    dont la courbe ne le justifie pas (voir docs/DECISIONS.md : GOLD/
    BTCUSD/ETHUSD restent plats sur 24h, aucune heure n'y est chère)."""
    curve = hourly_spread_by_asset(db_path)
    by_asset: Dict[str, List[Dict[str, object]]] = {}
    for row in curve:
        by_asset.setdefault(row["actif"], []).append(row)
    return {actif: compute_expensive_hours(rows, threshold) for actif, rows in by_asset.items()}


def is_expensive_hour(actif: str, hour: int, expensive_hours_by_asset: Dict[str, Set[int]]) -> bool:
    """Fonction de gate, pure — même contrat qu'un garde-fou existant
    (`circuit_breaker.evaluate_exposure_cap` etc.) : jamais un accès DB
    ici, la carte des heures chères est calculée une fois en amont
    (`compute_expensive_hours_by_asset`) et injectée."""
    return hour in expensive_hours_by_asset.get(actif, set())


def compute_expensive_hour_cost(db_path: str, expensive_hours_by_asset: Dict[str, Set[int]]) -> List[Dict[str, object]]:
    """Coût ÉCONOMISÉ (arithmétique certaine, jamais une prédiction) par
    (source, actif) : pour chaque trade RÉEL déjà ouvert pendant une
    heure chère de son actif, `(spread_a_cette_heure - spread_median_
    hors_heures_cheres) / distance_de_stop_de_CE_trade` — jamais un stop
    "typique" supposé, le stop réellement utilisé par ce trade précis
    (`trades.stop_loss_initial`/`prix_entree_prevu`, toujours connus dès
    l'ouverture). Somme ce coût sur tous les trades concernés = le
    nombre de R que ce filtre aurait évités SI il avait déjà existé —
    jamais un effet sur l'espérance de signal, qui reste non mesuré."""
    curve = hourly_spread_by_asset(db_path)
    spread_by_key = {(row["actif"], row["heure_utc"]): row["spread_moyen"] for row in curve}
    assets = {row["actif"] for row in curve}
    baseline_by_asset: Dict[str, Optional[float]] = {}
    for actif in assets:
        hors_fenetre = [
            spread_by_key[(actif, h)] for h in range(24)
            if (actif, h) in spread_by_key and h not in expensive_hours_by_asset.get(actif, set())
        ]
        baseline_by_asset[actif] = statistics.median(hors_fenetre) if hors_fenetre else None

    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT source, actif, ouvert_at, prix_entree_prevu, stop_loss_initial FROM trades "
            "WHERE prix_entree_prevu IS NOT NULL AND stop_loss_initial IS NOT NULL"
        ).fetchall()

    groups: Dict[Tuple[str, str], Dict[str, object]] = {}
    for row in rows:
        actif = row["actif"]
        expensive_hours = expensive_hours_by_asset.get(actif, set())
        if not expensive_hours:
            continue
        hour = utc_hour_from_timestamp(row["ouvert_at"])
        if hour not in expensive_hours:
            continue
        spread_ici = spread_by_key.get((actif, hour))
        baseline = baseline_by_asset.get(actif)
        stop_distance = abs(row["prix_entree_prevu"] - row["stop_loss_initial"])
        key = (row["source"], actif)
        bucket = groups.setdefault(key, {"n_trades_heure_chere": 0, "cout_r_total": 0.0, "cout_r_calculable": 0})
        bucket["n_trades_heure_chere"] += 1
        if spread_ici is not None and baseline is not None and stop_distance > 0:
            bucket["cout_r_total"] += max(0.0, spread_ici - baseline) / stop_distance
            bucket["cout_r_calculable"] += 1

    return [
        {"source": source, "actif": actif, **bucket}
        for (source, actif), bucket in sorted(groups.items())
    ]
