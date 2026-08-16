"""
backup_db.py — Sauvegarde de la base SQLite (data/assistant_trading.db).

La base EST le projet (historique de confiance, décisions de risque,
événements Go/No-Go) — perdre ce fichier sans copie récente est une perte
de données réelle, pas juste un inconvénient. Ce script fait une copie
cohérente via l'API de sauvegarde SQLite (safe même si la base est ouverte
en écriture ailleurs, contrairement à une simple copie de fichier), horodatée
dans data/backups/, et supprime les copies de plus de RETENTION_DAYS jours.

Usage :
    python scripts/backup_db.py

À planifier une fois par jour (cron sur le VPS, cf. checklist P0 — pas
encore fait tant que le VPS n'existe pas). En attendant, peut être lancé
manuellement.
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "assistant_trading.db"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
RETENTION_DAYS = 30


def backup_once(db_path: Path = DB_PATH, backup_dir: Path = BACKUP_DIR) -> Path:
    if not db_path.exists():
        print(f"Aucune base à sauvegarder : {db_path} n'existe pas encore.")
        sys.exit(0)

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = backup_dir / f"assistant_trading_{timestamp}.db"

    source_conn = sqlite3.connect(db_path)
    dest_conn = sqlite3.connect(dest_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    print(f"Sauvegarde créée : {dest_path}")
    return dest_path


def prune_old_backups(backup_dir: Path = BACKUP_DIR, retention_days: int = RETENTION_DAYS) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for backup_file in backup_dir.glob("assistant_trading_*.db"):
        mtime = datetime.fromtimestamp(backup_file.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            backup_file.unlink()
            print(f"Sauvegarde expirée supprimée : {backup_file}")


def main() -> None:
    backup_once()
    prune_old_backups()


if __name__ == "__main__":
    main()
