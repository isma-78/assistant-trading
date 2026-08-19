"""
envelope_store.py — Persistance DB de l'état des enveloppes de capital et
de la réserve globale (§2.3), en complément de `capital_manager.py` qui
reste volontairement pur et en mémoire (module critique, 100% couvert —
sa logique existante n'est pas modifiée pour cette intégration, voir
docs/DECISIONS.md). Fait le pont entre les tables `envelopes` /
`envelope_ledger` / `reserve_ledger` (§4.5 du CDC) et CapitalManager.

Aucune décision financière ici : ce module lit/écrit des soldes déjà
calculés par capital_manager.apply_trade_result(), il ne recalcule rien.
"""

from datetime import datetime, timezone
from typing import Tuple

from src.capital_manager import CapitalManager
from src.db import connection_scope


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_or_create_envelope(
    db_path: str, actif: str, mode: str, capital_initial: float, source: str = "stationx",
) -> Tuple[int, CapitalManager]:
    """Charge l'enveloppe (actif, mode, source) depuis la DB, ou la crée
    avec capital_initial si elle n'existe pas encore (première exécution
    pour cet actif/cette source). Retourne (envelope_id, CapitalManager
    positionné au solde courant persisté).

    `source` distingue les enveloppes du flux Station X ("stationx",
    valeur par défaut) de celles du Flux B (`"hypothesis"`, docs/HYPOTHESES.md)
    — jamais mélangées, chacune démarre et évolue indépendamment sur le
    même actif (§2.11 du CDC : "Métriques calculées séparément par
    source"). Voir docs/DECISIONS.md pour la migration de schéma associée
    (envelopes.source, contrainte UNIQUE élargie)."""
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT id, capital_courant FROM envelopes WHERE actif = ? AND mode = ? AND source = ?",
            (actif, mode, source),
        ).fetchone()
        if row is not None:
            return row["id"], CapitalManager(initial_balance=row["capital_courant"])

        now = _now()
        cursor = conn.execute(
            "INSERT INTO envelopes (actif, mode, source, capital_initial, capital_courant, nb_rechargements, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (actif, mode, source, capital_initial, capital_initial, now, now),
        )
        envelope_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO envelope_ledger (envelope_id, trade_id, montant_avant, montant_apres, type_mouvement, recorded_at) "
            "VALUES (?, NULL, 0, ?, 'init', ?)",
            (envelope_id, capital_initial, now),
        )
        return envelope_id, CapitalManager(initial_balance=capital_initial)


def load_reserve_total(db_path: str) -> float:
    """Dernier total de réserve connu (0.0 si aucun mouvement encore
    journalisé — la réserve démarre légitimement à 0€, voir
    capital_manager.apply_trade_result)."""
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT reserve_totale FROM reserve_ledger ORDER BY id DESC LIMIT 1").fetchone()
        return row["reserve_totale"] if row is not None else 0.0


def persist_trade_result(
    db_path: str, envelope_id: int, envelope: CapitalManager, trade_id: int,
    balance_before: float, reserve_share: float, reserve_total_after: float,
) -> None:
    """Journalise en DB le résultat déjà appliqué en mémoire par
    capital_manager.apply_trade_result() : nouveau solde d'enveloppe
    (`envelopes` + `envelope_ledger`), et mouvement de réserve
    (`reserve_ledger`) uniquement si reserve_share > 0 (trade gagnant)."""
    now = _now()
    with connection_scope(db_path) as conn:
        conn.execute(
            "UPDATE envelopes SET capital_courant = ?, updated_at = ? WHERE id = ?",
            (envelope.balance, now, envelope_id),
        )
        conn.execute(
            "INSERT INTO envelope_ledger (envelope_id, trade_id, montant_avant, montant_apres, type_mouvement, recorded_at) "
            "VALUES (?, ?, ?, ?, 'trade_pnl', ?)",
            (envelope_id, trade_id, balance_before, envelope.balance, now),
        )
        if reserve_share > 0:
            conn.execute(
                "INSERT INTO reserve_ledger (trade_id, montant_ajoute, reserve_totale, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (trade_id, reserve_share, reserve_total_after, now),
            )
