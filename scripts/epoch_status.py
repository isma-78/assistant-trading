"""
epoch_status.py — Compteur de confirmation par hypothèse (protocole E1,
25/09/2026, voir docs/DECISIONS.md). Lecture seule.

Pour chaque source, ne compte QUE les trades ouverts depuis le début de
l'époque en vigueur (table `hypothesis_epochs`, dernière ligne par source)
et sans `anomalie_technique` — jamais un trade d'une époque antérieure.
Seuil de verdict : max(30, 10 × nombre de variables ajustées), rappelé
ici, jamais appliqué automatiquement : ce script ne rend aucun verdict.

Usage : python scripts/epoch_status.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config

# Variables ajustées par hypothèse (pré-inscription du 29/08/2026 + toute
# idée déployée depuis, voir docs/DECISIONS.md) — à mettre à jour à chaque
# nouvelle variable déployée, jamais déduit des données.
ADJUSTED_VARIABLES = {
    "hypothesis_v2": 3,
    "hypothesis2_v2": 4,
    "hypothesis3_v2": 3,
    "hypothesis4_v2": 3,
    "hypothesis5_v2": 4,  # + TSMOM_LOOKBACK_DAYS (E2-H5, 25/09/2026)
}


def verdict_threshold(source: str) -> int:
    return max(30, 10 * ADJUSTED_VARIABLES.get(source, 5))


def main() -> None:
    conn = sqlite3.connect(load_config().db_path)
    conn.row_factory = sqlite3.Row
    epochs = {
        row["source"]: row
        for row in conn.execute("SELECT * FROM hypothesis_epochs ORDER BY started_at")
    }
    for source in ADJUSTED_VARIABLES:
        epoch = epochs.get(source)
        started = epoch["started_at"].replace("Z", "") if epoch else "2026-08-29"
        label = epoch["epoch"] if epoch else "pré-inscription du 29/08/2026"
        rows = conn.execute(
            "SELECT statut, r_multiple_total FROM trades WHERE source = ? AND ouvert_at >= ? "
            "AND anomalie_technique IS NULL AND statut IN ('ouvert', 'ferme', 'ferme_non_reconcilie')",
            (source, started),
        ).fetchall()
        closed = [r["r_multiple_total"] for r in rows if r["statut"] == "ferme" and r["r_multiple_total"] is not None]
        ghosts = sum(1 for r in rows if r["statut"] == "ferme_non_reconcilie")
        opened = sum(1 for r in rows if r["statut"] == "ouvert")
        print(
            f"{source:15s} époque={label:30s} depuis {started}  fermés={len(closed):3d}/{verdict_threshold(source)}"
            f"  ouverts={opened}  fantômes={ghosts}"
        )


if __name__ == "__main__":
    main()
