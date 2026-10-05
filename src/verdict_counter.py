"""
verdict_counter.py — Compteur de verdict forward par hypothèse (bilan du
05/10/2026, décisions 3, 4 et 7). Lecture seule.

Règles appliquées, toutes datées dans docs/DECISIONS.md :
- seuls comptent les trades ouverts depuis le début de l'époque en vigueur
  (`hypothesis_epochs`, dernière ligne par source), sans
  `anomalie_technique` (protocole E1, 25/09/2026) ;
- A9 / décision 4 : les trades CHFJPY sont EXCLUS du compteur de H2 à H5
  (pré-enregistrement du 29/08/2026 : CHFJPY exclue de leur univers de
  verdict) ; ils restent comptés à part, jamais effacés ;
- « réconcilié » (décision 7) = trade fermé avec un R ET dont chaque sortie
  effective (fraction > 0) porte un prix d'exécution broker
  (`trade_partials.prix_sortie_reel`). Un trade fermé sans ce prix est
  compté à part, jamais dans l'espérance ;
- jalons de verdict (décision 3) : 30, 40 et 53 trades réconciliés.

Ce module ne rend aucun verdict : il compte et calcule une espérance
descriptive.
"""

import sqlite3
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional

SOURCES = ("hypothesis_v2", "hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2", "hypothesis5_v2")
CHFJPY_EXCLUDED_SOURCES = ("hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2", "hypothesis5_v2")
MILESTONES = (30, 40, 53)
DEFAULT_EPOCH_START = "2026-08-29"


@dataclass(frozen=True)
class VerdictCount:
    source: str
    epoch: str
    started_at: str
    reconciled_r: List[float]
    closed_without_broker_price: int
    open_trades: int
    ghosts: int
    chfjpy_excluded: int

    @property
    def n(self) -> int:
        return len(self.reconciled_r)

    @property
    def mean_r(self) -> Optional[float]:
        return statistics.mean(self.reconciled_r) if self.reconciled_r else None


def counts_toward_verdict(source: str, actif: str) -> bool:
    """A9 : CHFJPY ne compte jamais pour le verdict de H2 à H5."""
    return not (actif == "CHFJPY" and source in CHFJPY_EXCLUDED_SOURCES)


def reached_milestones(previous_n: int, current_n: int, milestones=MILESTONES) -> List[int]:
    """Jalons franchis entre deux relevés (strictement après `previous_n`,
    au plus `current_n`)."""
    return [m for m in milestones if previous_n < m <= current_n]


def _latest_epochs(conn) -> Dict[str, sqlite3.Row]:
    return {row["source"]: row for row in conn.execute("SELECT * FROM hypothesis_epochs ORDER BY started_at, id")}


def count_source(conn, source: str, epochs: Optional[Dict[str, sqlite3.Row]] = None) -> VerdictCount:
    conn.row_factory = sqlite3.Row
    epochs = epochs if epochs is not None else _latest_epochs(conn)
    epoch = epochs.get(source)
    started = epoch["started_at"].replace("Z", "") if epoch else DEFAULT_EPOCH_START
    label = epoch["epoch"] if epoch else "pré-inscription du 29/08/2026"
    rows = conn.execute(
        "SELECT id, actif, statut, r_multiple_total FROM trades WHERE source = ? AND ouvert_at >= ? "
        "AND anomalie_technique IS NULL AND statut IN ('ouvert', 'ferme', 'ferme_non_reconcilie')",
        (source, started),
    ).fetchall()
    reconciled, unpriced, opened, ghosts, excluded = [], 0, 0, 0, 0
    for row in rows:
        if not counts_toward_verdict(source, row["actif"]):
            excluded += 1
            continue
        if row["statut"] == "ouvert":
            opened += 1
        elif row["statut"] == "ferme_non_reconcilie":
            ghosts += 1
        elif row["r_multiple_total"] is not None:
            missing = conn.execute(
                "SELECT COUNT(*) FROM trade_partials WHERE trade_id = ? AND fraction > 0 AND prix_sortie_reel IS NULL",
                (row["id"],),
            ).fetchone()[0]
            if missing:
                unpriced += 1
            else:
                reconciled.append(row["r_multiple_total"])
    return VerdictCount(source, label, started, reconciled, unpriced, opened, ghosts, excluded)


def count_all(db_path: str) -> List[VerdictCount]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        epochs = _latest_epochs(conn)
        return [count_source(conn, source, epochs) for source in SOURCES]
    finally:
        conn.close()
