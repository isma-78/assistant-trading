"""
trade_features_store.py — Collecte de la variable #1 du §3.8 du CDC :
"alignement avec le biais de la Matinale" (docs/CDC_v4.md §3.8, table
`trade_features` du §4.5, jamais alimentée avant ce module — voir
docs/DECISIONS.md, 20/08/2026).

Collecte UNIQUEMENT (demande explicite d'Ismaël) : `align_matinale`
n'influence aucune décision de `risk_engine`, `validator`, ni `executor` —
il est calculé et journalisé APRÈS qu'un trade a déjà été ouvert, jamais
avant ni en entrée d'une décision. Aucune conclusion statistique n'est
tirée avant le seuil de 10 trades/variable (invariant #10) — ce module ne
fait qu'écrire la donnée, aucun code de ce projet ne la lit encore pour
décider de quoi que ce soit.

Les 4 autres variables du §3.8 (align_tendance_fond, ratio_gain_risque_
prevu, proximite_macro, volatilite_relative) ne sont PAS collectées par ce
module ni par aucun autre à ce jour (colonnes présentes dans le schéma,
jamais écrites — voir docs/DECISIONS.md pour le constat complet).

Deux couches, comme le reste du projet :
- compute_align_matinale() : pure, déterministe, 100% testée.
- get_latest_matinale_biais()/record_align_matinale() : I/O DB.
"""

import logging
from typing import Optional

from src.db import connection_scope

logger = logging.getLogger(__name__)


def compute_align_matinale(direction: str, biais: Optional[str]) -> Optional[bool]:
    """Aligné (True) si le sens du trade correspond au biais déclaré de la
    dernière Matinale sur l'actif (long+haussier ou short+baissier), opposé
    (False) dans le cas contraire (long+baissier ou short+haussier). None
    ("non disponible") si `biais` est absent, "neutre" ou "indetermine" —
    un biais non directionnel ne peut être ni aligné ni opposé, jamais
    deviné (fail-safe, invariant #7).

    Encodage retenu pour trade_features.align_matinale (INTEGER, §4.5 du
    CDC, colonne déjà existante) : 1=aligné, 0=opposé, NULL=non disponible
    — voir docs/DECISIONS.md pour le choix de cet encodage tri-état sur une
    colonne binaire plutôt qu'une migration de schéma."""
    if biais not in ("haussier", "baissier"):
        return None
    if direction == "long":
        return biais == "haussier"
    if direction == "short":
        return biais == "baissier"
    return None


def get_latest_matinale_biais(db_path: str, actif: str, before: str) -> Optional[str]:
    """Biais déclaré (matinale_summaries.sentiment_tag — "Sentiment X" ou
    "Biais X.", voir docs/DECISIONS.md) de la dernière Matinale publiée sur
    `actif` avant ou au moment de `before` (horodatage ISO d'ouverture du
    trade) — jamais une Matinale future par rapport au trade, pour ne
    jamais introduire de biais rétrospectif dans une future analyse
    statistique. None si aucune Matinale valide n'existe sur cet actif à
    cette date, ou si le tag n'a pas pu être résolu ce jour-là."""
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT sentiment_tag FROM matinale_summaries "
            "WHERE actif = ? AND published_at <= ? "
            "ORDER BY published_at DESC LIMIT 1",
            (actif, before),
        ).fetchone()
    return row["sentiment_tag"] if row is not None else None


def record_align_matinale(db_path: str, trade_id: int, align_matinale: Optional[bool]) -> None:
    """Journalise trade_features.align_matinale pour un trade qui vient
    d'être ouvert. Une ligne par trade (trade_id est la clé primaire de
    `trade_features`) — INSERT unique, jamais de mise à jour a posteriori
    (le biais est celui connu AU MOMENT de l'ouverture, il ne doit jamais
    dériver après coup). Les 4 autres colonnes de `trade_features` restent
    NULL (non collectées, voir docs/DECISIONS.md)."""
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trade_features (trade_id, align_matinale) VALUES (?, ?)",
            (trade_id, None if align_matinale is None else int(align_matinale)),
        )


def record_align_matinale_for_trade(db_path: str, trade_id: int, actif: str, direction: str, opened_at: str) -> Optional[bool]:
    """Point d'entrée : à appeler juste après qu'executor.open_signal() a
    inséré un nouveau trade (Station X ou Flux B, aucune distinction —
    §3.8 s'applique aux deux). Retourne la valeur calculée (pour log/tests),
    None si aucune Matinale n'est disponible sur cet actif."""
    biais = get_latest_matinale_biais(db_path, actif, opened_at)
    align_matinale = compute_align_matinale(direction, biais)
    record_align_matinale(db_path, trade_id, align_matinale)
    return align_matinale
