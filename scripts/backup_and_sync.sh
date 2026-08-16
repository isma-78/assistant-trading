#!/usr/bin/env bash
# Sauvegarde SQLite quotidienne + synchronisation hors VPS (guide P0 §7).
# Lancé par cron sur le VPS. Le remote rclone "scaleway" doit être configuré
# au préalable via `rclone config` (Scaleway Object Storage, bucket
# assistant-trading-backups, région fr-par) — jamais par ce script.
set -euo pipefail

# cron s'exécute avec un PATH minimal ; rclone est installé en espace
# utilisateur (~/.local/bin), pas dans le PATH par défaut de cron.
export PATH="$HOME/.local/bin:$PATH"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

venv/bin/python scripts/backup_db.py

# backup_db.py ne crée data/backups/ que s'il y a une base à sauvegarder
# (sys.exit(0) prématuré sinon) — garantir le dossier avant rclone sync,
# qui échoue si le chemin source n'existe pas du tout.
mkdir -p data/backups

rclone sync data/backups/ scaleway:assistant-trading-backups --fast-list
