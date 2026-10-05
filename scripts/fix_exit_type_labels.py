"""
fix_exit_type_labels.py — A6 du bilan du 05/10/2026 : corrige l'ÉTIQUETTE
`trades.exit_type` des trades H5/L5 (`hypothesis5_v2`), marqués
`tp_partiel` alors que H5 est 100% trailing par pré-enregistrement.

Étiquette seulement : ne touche ni le R, ni le statut, ni le P&L, ni aucun
trade d'une autre source. Idempotent. Par défaut, simple simulation qui
affiche ce qui serait changé ; `--apply` écrit (faire une sauvegarde
avant, voir docs/RUNBOOK_REDEPLOIEMENT_10-10.md).

Usage :
    venv/bin/python scripts/fix_exit_type_labels.py            # simulation
    venv/bin/python scripts/fix_exit_type_labels.py --apply    # écriture
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WHERE = "source = 'hypothesis5_v2' AND exit_type = 'tp_partiel'"


def fix_labels(db_path: str, apply: bool) -> int:
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM trades WHERE {WHERE}").fetchone()[0]
        if apply and count:
            conn.execute(f"UPDATE trades SET exit_type = 'trailing_pur' WHERE {WHERE}")
            conn.commit()
        return count
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from src.config import load_config
    count = fix_labels(load_config().db_path, args.apply)
    verb = "corrigé(s)" if args.apply else "à corriger (simulation, relancer avec --apply)"
    print(f"{count} trade(s) H5 {verb} : exit_type tp_partiel -> trailing_pur")


if __name__ == "__main__":
    main()
