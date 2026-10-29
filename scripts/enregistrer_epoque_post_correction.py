"""
enregistrer_epoque_post_correction.py — enregistre la date de début
« post-correction » (docs/corrections_hypotheses_2026-10-29.md §5) dans
`hypothesis_epochs`, une ligne par source H1-H5. Écriture de métadonnées
uniquement : aucun appel broker. Idempotent (une époque déjà présente pour
une source n'est jamais dupliquée ni modifiée).

Usage : python scripts/enregistrer_epoque_post_correction.py <db_path> [horodatage_UTC_ISO] [libellé] [description]
"""
import sqlite3
import sys
from datetime import datetime, timezone

EPOCH_LABEL = "P1_29-10"
SOURCES = ("hypothesis_v2", "hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2", "hypothesis5_v2")
DESCRIPTION = (
    "Post-correction du 29/10/2026 : mapping hypothèse->actifs d'entrée (adéquation structurelle), "
    "filtre de coût spread/stop > 0,10 R, refus des ordres limites déjà exécutables. "
    "Évaluation future = trades ouverts après cet instant uniquement."
)


def register(db_path: str, started_at: str, label: str = EPOCH_LABEL, description: str = DESCRIPTION) -> int:
    conn = sqlite3.connect(db_path)
    inserted = 0
    try:
        for source in SOURCES:
            exists = conn.execute(
                "SELECT 1 FROM hypothesis_epochs WHERE source = ? AND epoch = ?", (source, label),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO hypothesis_epochs (source, epoch, started_at, description) VALUES (?, ?, ?, ?)",
                (source, label, started_at, description),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def main(argv) -> int:
    db_path = argv[1]
    started_at = argv[2] if len(argv) > 2 else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    label = argv[3] if len(argv) > 3 else EPOCH_LABEL
    description = argv[4] if len(argv) > 4 else DESCRIPTION
    n = register(db_path, started_at, label, description)
    print(f"{n} époque(s) {label} enregistrée(s) à partir de {started_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
