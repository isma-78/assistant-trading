"""
causal_decomposition.py — Attribution causale DÉTERMINISTE par trade
(27/08/2026, voir docs/DECISIONS.md). Répond à un manque identifié le
même jour : `causal_analyzer.py` ne se déclenche que sur un événement de
coupe-circuit (post-mortem d'incident) — aucun module n'expliquait, pour
un trade normal, D'OÙ VIENT l'écart entre le résultat théorique et le
résultat réel.

Décomposition (aucun jugement, aucun LLM — invariants #1/#2, pure
arithmétique) :

    R_réalisé = R_théorique - coût_entrée - coût_sortie - dérive_gestion

- R_théorique : le R qu'aurait rendu le trade en exécutant EXACTEMENT
  les prix décidés par la stratégie (prix limite demandé à l'entrée,
  prix déclenché par la gestion à la sortie), sans aucun frais ni
  slippage.
- coût_entrée : écart (en unités de R) entre le prix limite demandé et
  le prix RÉELLEMENT rempli (`trades.prix_entree_reel`, déjà en base
  depuis le palier P2). Toujours mesurable pour un trade rempli.
- coût_sortie : même écart côté sortie, entre le prix théorique de
  gestion (`trade_partials.prix_sortie`) et le prix RÉELLEMENT exécuté
  (`trade_partials.prix_sortie_reel`, ajouté le 27/08/2026 — voir
  `capital_client.close_position`/`executor._apply_management_action`).
  None tant que cette colonne n'est pas renseignée.
- dérive_gestion : **identité de cohérence, PAS une mesure indépendante**
  (correctif du 28/08/2026, voir docs/DECISIONS.md — trouvé en vérifiant
  l'identité sur un trade réel, comme demandé). `R_réalisé` est calculé
  ICI directement depuis les prix RÉELS (`sign×(exit_réel-entrée_réel)/
  stop`), jamais réutilisé depuis `trade_partials.r_atteint` (qui mélange
  entrée RÉELLE et niveau de sortie THÉORIQUE — base incohérente qui
  rendait `dérive_gestion` algébriquement égal à `-coût_sortie` À CHAQUE
  FOIS, quelle que soit la donnée, un artefact de calcul déguisé en
  mesure). Avec un `R_réalisé` cohérent, `dérive_gestion` est
  algébriquement **toujours ≈0 pour une jambe isolée** (coût_entrée +
  coût_sortie expliquent alors EXACTEMENT tout l'écart, par construction
  arithmétique — aucune notion de "décalage de trailing/polling" n'est
  capturable à ce niveau, qui ne compare que des PRIX, jamais des
  décisions de gestion prises à des instants différents). Conservé
  néanmoins : un `dérive_gestion` significativement non-nul est
  IMPOSSIBLE si toutes les entrées sont cohérentes — sa seule valeur
  restante est de servir de **détecteur d'incohérence de données**,
  jamais de mesure de "gestion".

Deux couches, même convention que risk_engine.py/backtest_engine.py :
- Calcul pur (`decompose_trade_leg`, `aggregate_trade_decomposition`,
  `is_cout_sortie_plausible`) : 100% de couverture.
- Orchestration I/O (`compute_trade_causal_decomposition`,
  `persist_trade_causal_decomposition`, `aggregate_by_month`) : lecture/
  écriture DB, pas soumise à la même exigence littérale.

**Garde-fou de plausibilité (28/08/2026)** : une confirmation de clôture
périmée (voir `capital_client.close_position`) peut produire un
`coût_sortie` numériquement énorme (ex. trade 14239 : ratio coût_sortie/
spread ≈ 25, contre <5 pour un cas réel authentique mesuré le même jour)
— `is_cout_sortie_plausible` (seuil de RATIO, comparable entre actifs,
jamais un seuil absolu) marque la ligne `invalide` au lieu de compter sur
la discipline de ne jamais relire une ligne signalée dans un fichier de
notes.

**`coût_sortie` décomposé en deux (28/08/2026, point 2)** : `dérive_
gestion` ne pouvant capturer aucun décalage de DÉCISION (voir ci-dessus),
la question "où part le coût de sortie" se répond en instrumentant le
moment de la DÉCISION elle-même — `executor._apply_management_action`
capture désormais `p_déclenchement` (prix vu par le système au moment
où `evaluate_position_management` décide, `snapshot.mid`) et
`t_déclenchement` (`snapshot.captured_at_broker`, l'horodatage RÉEL du
broker pour ce prix, jamais notre horloge). `decompose_gestion_delay`
sépare alors `coût_sortie` (inchangé, toujours `prix_théorique −
prix_réel`) en :
- `survol_polling` = `prix_théorique − p_déclenchement` : ce que le
  marché avait déjà dépassé la cible AVANT même que le système ne
  s'en aperçoive (borné par l'intervalle de sondage, ~30-60s) ;
- `délai_broker` = `p_déclenchement − prix_réel` : l'écart entre ce que
  le système voyait au moment de sa décision et ce qui a RÉELLEMENT été
  exécuté (latence broker/réseau entre la demande et l'exécution).

Identité par construction : `survol_polling + délai_broker == coût_
sortie` (vérifiée par test) — ce n'est PAS un troisième terme de
l'identité principale du module, seulement une décomposition
supplémentaire de `coût_sortie` déjà existant. `None` (les deux) si
`p_déclenchement` est absent (trades antérieurs à ce déploiement,
clôtures d'urgence sans décision de gestion) — jamais une valeur
imputée, même convention que le reste du module."""

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from src.db import connection_scope

# Ratio coût_sortie/spread au-delà duquel une ligne est jugée implausible
# (28/08/2026, voir docs/DECISIONS.md) — calibré sur le cas réel corrompu
# du 28/08 (ratio ≈25) vs les cas réels authentiques du même jour
# (ratio <5). Comparable entre actifs par construction (jamais un seuil
# absolu en unités de prix, qui varierait d'un facteur >10000 entre
# EURUSD et BTCUSD).
MAX_PLAUSIBLE_COUT_SORTIE_SPREAD_RATIO = 10.0


@dataclass(frozen=True)
class TradeLegDecomposition:
    r_theoretical: float
    cout_entree: Optional[float]
    cout_sortie: Optional[float]
    derive_gestion: Optional[float]
    survol_polling: Optional[float] = None
    delai_broker: Optional[float] = None


@dataclass(frozen=True)
class TradeCausalDecomposition:
    r_theoretical: float
    cout_entree: Optional[float]
    cout_sortie: Optional[float]
    derive_gestion: Optional[float]
    invalide: bool = False
    survol_polling: Optional[float] = None
    delai_broker: Optional[float] = None


def decompose_gestion_delay(
    direction: str,
    exit_price_theoretical: float,
    p_declenchement: Optional[float],
    exit_price_real: Optional[float],
    stop_distance: float,
) -> Tuple[Optional[float], Optional[float]]:
    """Sépare `coût_sortie` en (`survol_polling`, `délai_broker`) — voir
    docstring du module. `None, None` si `p_declenchement` ou
    `exit_price_real` manque (jamais une valeur imputée à partir d'une
    seule composante connue)."""
    if p_declenchement is None or exit_price_real is None:
        return None, None
    if direction == "long":
        sign = 1.0
    elif direction == "short":
        sign = -1.0
    else:
        raise ValueError(f"direction inconnue : {direction!r}")
    survol_polling = sign * (exit_price_theoretical - p_declenchement) / stop_distance
    delai_broker = sign * (p_declenchement - exit_price_real) / stop_distance
    return survol_polling, delai_broker


def decompose_trade_leg(
    direction: str,
    entry_price_theoretical: float,
    entry_price_real: Optional[float],
    exit_price_theoretical: float,
    exit_price_real: Optional[float],
    stop_distance: float,
    p_declenchement: Optional[float] = None,
) -> TradeLegDecomposition:
    """Décompose UNE jambe (une ligne `trade_partials`, ou le trade entier
    s'il n'a qu'une seule sortie). `stop_distance` : distance de stop
    initiale en unités de prix (dénominateur de R, toujours > 0 —
    invariant #5, un stop ne peut jamais être nul par construction de
    `risk_engine.py`).

    `direction` : "long" ou "short", sinon ValueError (fail-safe côté
    appelant — cette fonction ne devine jamais un sens par défaut).

    Le R RÉELLEMENT réalisé n'est PAS un paramètre (correctif du
    28/08/2026, voir docstring du module) : calculé ICI, uniquement
    depuis `entry_price_real`/`exit_price_real` quand les deux sont
    connus — jamais réutilisé d'un champ externe (`trades.r_atteint`) qui
    mélangerait des bases de prix incohérentes.

    `p_declenchement` (28/08/2026, point 2) : prix vu par le système au
    moment de la décision de gestion — voir `decompose_gestion_delay`."""
    if stop_distance <= 0:
        raise ValueError(f"stop_distance doit être > 0, reçu {stop_distance!r}")
    if direction == "long":
        sign = 1.0
    elif direction == "short":
        sign = -1.0
    else:
        raise ValueError(f"direction inconnue : {direction!r}")

    r_theoretical = sign * (exit_price_theoretical - entry_price_theoretical) / stop_distance

    cout_entree: Optional[float] = None
    if entry_price_real is not None:
        cout_entree = sign * (entry_price_real - entry_price_theoretical) / stop_distance

    cout_sortie: Optional[float] = None
    if exit_price_real is not None:
        cout_sortie = sign * (exit_price_theoretical - exit_price_real) / stop_distance

    derive_gestion: Optional[float] = None
    if entry_price_real is not None and exit_price_real is not None:
        r_realized = sign * (exit_price_real - entry_price_real) / stop_distance
        derive_gestion = r_theoretical - r_realized - cout_entree - cout_sortie

    survol_polling, delai_broker = decompose_gestion_delay(
        direction, exit_price_theoretical, p_declenchement, exit_price_real, stop_distance,
    )

    return TradeLegDecomposition(
        r_theoretical=r_theoretical, cout_entree=cout_entree, cout_sortie=cout_sortie, derive_gestion=derive_gestion,
        survol_polling=survol_polling, delai_broker=delai_broker,
    )


def aggregate_trade_decomposition(
    legs: List[Tuple[float, TradeLegDecomposition]],
) -> TradeCausalDecomposition:
    """Agrège plusieurs jambes (fraction, décomposition) au prorata de
    `fraction` (même convention que `risk_engine.compute_weighted_r_
    multiple` — TP1/TP2/TP3 pondérés par leur part de la position).

    Un composant devient None dès qu'IL MANQUE sur AU MOINS une jambe —
    jamais une moyenne partielle silencieuse sur les jambes connues
    (fail-safe : mieux vaut "inconnu pour ce trade" que "faux parce que
    incomplet"). `invalide` reste à False (défaut) — la plausibilité se
    juge en orchestration, avec le spread de l'actif, hors de portée de
    cette fonction pure. Lève ValueError si `legs` est vide."""
    if not legs:
        raise ValueError("Au moins une jambe est requise.")

    r_theoretical = sum(fraction * leg.r_theoretical for fraction, leg in legs)

    cout_entree: Optional[float] = None
    if all(leg.cout_entree is not None for _, leg in legs):
        cout_entree = sum(fraction * leg.cout_entree for fraction, leg in legs)

    cout_sortie: Optional[float] = None
    if all(leg.cout_sortie is not None for _, leg in legs):
        cout_sortie = sum(fraction * leg.cout_sortie for fraction, leg in legs)

    derive_gestion: Optional[float] = None
    if all(leg.derive_gestion is not None for _, leg in legs):
        derive_gestion = sum(fraction * leg.derive_gestion for fraction, leg in legs)

    survol_polling: Optional[float] = None
    if all(leg.survol_polling is not None for _, leg in legs):
        survol_polling = sum(fraction * leg.survol_polling for fraction, leg in legs)

    delai_broker: Optional[float] = None
    if all(leg.delai_broker is not None for _, leg in legs):
        delai_broker = sum(fraction * leg.delai_broker for fraction, leg in legs)

    return TradeCausalDecomposition(
        r_theoretical=r_theoretical, cout_entree=cout_entree, cout_sortie=cout_sortie, derive_gestion=derive_gestion,
        survol_polling=survol_polling, delai_broker=delai_broker,
    )


def is_cout_sortie_plausible(
    cout_sortie: Optional[float], spread: Optional[float], stop_distance: float,
    max_ratio: float = MAX_PLAUSIBLE_COUT_SORTIE_SPREAD_RATIO,
) -> bool:
    """`cout_sortie` est en R (÷ stop_distance) ; reconverti en unités de
    prix (`× stop_distance`) puis comparé au spread — le RATIO est
    comparable entre actifs, jamais le coût absolu (25/08/2026, écart
    EURUSD/BTCUSD déjà documenté). Toujours plausible (True) si
    `cout_sortie` ou `spread` est None, ou `spread<=0` : l'ABSENCE de
    donnée n'est jamais un motif d'invalidation (fail-safe distinct du
    cas "donnée présente mais aberrante", seul visé ici)."""
    if cout_sortie is None or spread is None or spread <= 0:
        return True
    ratio = abs(cout_sortie) * stop_distance / spread
    return ratio <= max_ratio


def compute_trade_causal_decomposition(db_path: str, trade_id: int) -> Optional[TradeCausalDecomposition]:
    """Construit les jambes d'un trade clôturé depuis `trades`/
    `trade_partials` et retourne sa décomposition agrégée, `invalide`
    posé selon `is_cout_sortie_plausible` (spread pris sur
    `market_snapshots` du signal d'origine du trade — absent, jamais
    invalidant, voir sa docstring). None si le trade n'existe pas, n'est
    pas clôturé, ou n'a aucune jambe de sortie persistée (jamais géré
    comme une décomposition à zéro par défaut)."""
    with connection_scope(db_path) as conn:
        trade = conn.execute(
            "SELECT direction, prix_entree_prevu, prix_entree_reel, stop_loss_initial, signal_id "
            "FROM trades WHERE id = ? AND statut = 'ferme'", (trade_id,),
        ).fetchone()
        if trade is None:
            return None
        partials = conn.execute(
            "SELECT fraction, prix_sortie, prix_sortie_reel, p_declenchement FROM trade_partials WHERE trade_id = ?",
            (trade_id,),
        ).fetchall()
        spread_row = conn.execute(
            "SELECT spread FROM market_snapshots WHERE signal_id = ?", (trade["signal_id"],),
        ).fetchone()
    if not partials:
        return None

    entry_theoretical = trade["prix_entree_prevu"]
    entry_real = trade["prix_entree_reel"]
    stop_distance = abs(entry_theoretical - trade["stop_loss_initial"])
    if stop_distance <= 0:
        return None

    legs = [
        (
            row["fraction"],
            decompose_trade_leg(
                direction=trade["direction"],
                entry_price_theoretical=entry_theoretical,
                entry_price_real=entry_real,
                exit_price_theoretical=row["prix_sortie"],
                exit_price_real=row["prix_sortie_reel"],
                stop_distance=stop_distance,
                p_declenchement=row["p_declenchement"],
            ),
        )
        for row in partials
    ]
    decomposition = aggregate_trade_decomposition(legs)
    spread = spread_row["spread"] if spread_row is not None else None
    plausible = is_cout_sortie_plausible(decomposition.cout_sortie, spread, stop_distance)
    return replace(decomposition, invalide=not plausible)


def persist_trade_causal_decomposition(db_path: str, trade_id: int, decomposition: TradeCausalDecomposition, computed_at: str) -> None:
    """Écrit (remplace) la décomposition d'un trade — idempotent, appelé
    à chaque clôture ou en rattrapage (ne modifie jamais `trades`/
    `trade_partials`, table dédiée séparée)."""
    with connection_scope(db_path) as conn:
        conn.execute("DELETE FROM trade_causal_decomposition WHERE trade_id = ?", (trade_id,))
        conn.execute(
            "INSERT INTO trade_causal_decomposition "
            "(trade_id, r_theoretical, cout_entree, cout_sortie, derive_gestion, invalide, "
            "survol_polling, delai_broker, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade_id, decomposition.r_theoretical, decomposition.cout_entree,
                decomposition.cout_sortie, decomposition.derive_gestion, int(decomposition.invalide),
                decomposition.survol_polling, decomposition.delai_broker, computed_at,
            ),
        )


def aggregate_by_hypothesis_asset_month(db_path: str) -> List[Dict[str, object]]:
    """Agrège `trade_causal_decomposition` par (source, actif, mois) —
    le livrable demandé : où part l'argent, jamais une espérance globale
    de plus. **Exclut automatiquement toute ligne `invalide=1`**
    (28/08/2026 — garde-fou déterministe, plus une discipline
    manuelle de relecture). `cout_sortie`/`derive_gestion` restent NULL
    pour tout groupe dont AU MOINS un trade (valide) n'a pas encore de
    sortie réelle capturée (moyenne honnête sur les seules valeurs
    connues aurait masqué un manque de données comme un résultat)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT t.source AS source, t.actif AS actif, substr(t.ferme_at, 1, 7) AS mois, "
            "d.r_theoretical AS r_theoretical, d.cout_entree AS cout_entree, "
            "d.cout_sortie AS cout_sortie, d.derive_gestion AS derive_gestion, "
            "d.survol_polling AS survol_polling, d.delai_broker AS delai_broker "
            "FROM trade_causal_decomposition d JOIN trades t ON t.id = d.trade_id "
            "WHERE d.invalide = 0"
        ).fetchall()

    groups: Dict[Tuple[str, str, str], List[dict]] = {}
    for row in rows:
        key = (row["source"], row["actif"], row["mois"])
        groups.setdefault(key, []).append(dict(row))

    results = []
    for (source, actif, mois), items in sorted(groups.items()):
        n = len(items)
        r_theo_mean = sum(item["r_theoretical"] for item in items) / n
        cout_entree_vals = [item["cout_entree"] for item in items]
        cout_sortie_vals = [item["cout_sortie"] for item in items]
        derive_vals = [item["derive_gestion"] for item in items]
        survol_vals = [item["survol_polling"] for item in items]
        delai_vals = [item["delai_broker"] for item in items]
        results.append({
            "source": source, "actif": actif, "mois": mois, "n": n,
            "r_theorique_moyen": r_theo_mean,
            "cout_entree_moyen": (sum(cout_entree_vals) / n) if all(v is not None for v in cout_entree_vals) else None,
            "cout_sortie_moyen": (sum(cout_sortie_vals) / n) if all(v is not None for v in cout_sortie_vals) else None,
            "derive_gestion_moyenne": (sum(derive_vals) / n) if all(v is not None for v in derive_vals) else None,
            "survol_polling_moyen": (sum(survol_vals) / n) if all(v is not None for v in survol_vals) else None,
            "delai_broker_moyen": (sum(delai_vals) / n) if all(v is not None for v in delai_vals) else None,
        })
    return results
