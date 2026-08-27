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
  None tant que cette colonne n'est pas renseignée (tout trade clôturé
  AVANT ce déploiement, structurellement).
- dérive_gestion : résidu arithmétique (jamais mesuré indépendamment) —
  absorbe tout ce que les deux coûts de prix n'expliquent pas
  (décalage de polling entre déclenchement et exécution, séquencement
  des sorties partielles). None dès que `coût_sortie` est None (le
  résidu ne peut pas être isolé sans lui).

Deux couches, même convention que risk_engine.py/backtest_engine.py :
- Calcul pur (`decompose_trade_leg`, `aggregate_trade_decomposition`) :
  100% de couverture.
- Orchestration I/O (`compute_trade_causal_decomposition`,
  `persist_trade_causal_decomposition`, `aggregate_by_month`) : lecture/
  écriture DB, pas soumise à la même exigence littérale.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.db import connection_scope


@dataclass(frozen=True)
class TradeLegDecomposition:
    r_theoretical: float
    cout_entree: Optional[float]
    cout_sortie: Optional[float]
    derive_gestion: Optional[float]


@dataclass(frozen=True)
class TradeCausalDecomposition:
    r_theoretical: float
    cout_entree: Optional[float]
    cout_sortie: Optional[float]
    derive_gestion: Optional[float]


def decompose_trade_leg(
    direction: str,
    entry_price_theoretical: float,
    entry_price_real: Optional[float],
    exit_price_theoretical: float,
    exit_price_real: Optional[float],
    stop_distance: float,
    r_realized: float,
) -> TradeLegDecomposition:
    """Décompose UNE jambe (une ligne `trade_partials`, ou le trade entier
    s'il n'a qu'une seule sortie). `stop_distance` : distance de stop
    initiale en unités de prix (dénominateur de R, toujours > 0 —
    invariant #5, un stop ne peut jamais être nul par construction de
    `risk_engine.py`).

    `direction` : "long" ou "short", sinon ValueError (fail-safe côté
    appelant — cette fonction ne devine jamais un sens par défaut)."""
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
    if cout_entree is not None and cout_sortie is not None:
        derive_gestion = r_theoretical - r_realized - cout_entree - cout_sortie

    return TradeLegDecomposition(
        r_theoretical=r_theoretical, cout_entree=cout_entree, cout_sortie=cout_sortie, derive_gestion=derive_gestion,
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
    incomplet"). Lève ValueError si `legs` est vide."""
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

    return TradeCausalDecomposition(
        r_theoretical=r_theoretical, cout_entree=cout_entree, cout_sortie=cout_sortie, derive_gestion=derive_gestion,
    )


def compute_trade_causal_decomposition(db_path: str, trade_id: int) -> Optional[TradeCausalDecomposition]:
    """Construit les jambes d'un trade clôturé depuis `trades`/
    `trade_partials` et retourne sa décomposition agrégée. None si le
    trade n'existe pas, n'est pas clôturé, ou n'a aucune jambe de sortie
    persistée (jamais géré comme une décomposition à zéro par défaut)."""
    with connection_scope(db_path) as conn:
        trade = conn.execute(
            "SELECT direction, prix_entree_prevu, prix_entree_reel, stop_loss_initial "
            "FROM trades WHERE id = ? AND statut = 'ferme'", (trade_id,),
        ).fetchone()
        if trade is None:
            return None
        partials = conn.execute(
            "SELECT fraction, r_atteint, prix_sortie, prix_sortie_reel FROM trade_partials WHERE trade_id = ?",
            (trade_id,),
        ).fetchall()
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
                r_realized=row["r_atteint"],
            ),
        )
        for row in partials
    ]
    return aggregate_trade_decomposition(legs)


def persist_trade_causal_decomposition(db_path: str, trade_id: int, decomposition: TradeCausalDecomposition, computed_at: str) -> None:
    """Écrit (remplace) la décomposition d'un trade — idempotent, appelé
    à chaque clôture ou en rattrapage (ne modifie jamais `trades`/
    `trade_partials`, table dédiée séparée)."""
    with connection_scope(db_path) as conn:
        conn.execute("DELETE FROM trade_causal_decomposition WHERE trade_id = ?", (trade_id,))
        conn.execute(
            "INSERT INTO trade_causal_decomposition "
            "(trade_id, r_theoretical, cout_entree, cout_sortie, derive_gestion, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                trade_id, decomposition.r_theoretical, decomposition.cout_entree,
                decomposition.cout_sortie, decomposition.derive_gestion, computed_at,
            ),
        )


def aggregate_by_hypothesis_asset_month(db_path: str) -> List[Dict[str, object]]:
    """Agrège `trade_causal_decomposition` par (source, actif, mois) —
    le livrable demandé : où part l'argent, jamais une espérance globale
    de plus. `cout_sortie`/`derive_gestion` restent NULL pour tout groupe
    dont AU MOINS un trade n'a pas encore de sortie réelle capturée
    (moyenne honnête sur les seules valeurs connues aurait masqué un
    manque de données comme un résultat)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT t.source AS source, t.actif AS actif, substr(t.ferme_at, 1, 7) AS mois, "
            "d.r_theoretical AS r_theoretical, d.cout_entree AS cout_entree, "
            "d.cout_sortie AS cout_sortie, d.derive_gestion AS derive_gestion "
            "FROM trade_causal_decomposition d JOIN trades t ON t.id = d.trade_id"
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
        results.append({
            "source": source, "actif": actif, "mois": mois, "n": n,
            "r_theorique_moyen": r_theo_mean,
            "cout_entree_moyen": (sum(cout_entree_vals) / n) if all(v is not None for v in cout_entree_vals) else None,
            "cout_sortie_moyen": (sum(cout_sortie_vals) / n) if all(v is not None for v in cout_sortie_vals) else None,
            "derive_gestion_moyenne": (sum(derive_vals) / n) if all(v is not None for v in derive_vals) else None,
        })
    return results
