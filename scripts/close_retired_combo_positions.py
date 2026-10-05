"""
close_retired_combo_positions.py — A5 du bilan du 05/10/2026 : clôture des
4 positions du combo H2 retiré le 25/09 (trades 14877 CHFJPY, 15948 BTCUSD,
15954 ETHUSD, 16329 GBPUSD), qui occupent du plafond de cluster.

NON lancé par l'agent : le mandat du 05/10 conditionnait cette clôture à la
vérification broker A1, qui n'a pas pu être exécutée (création de session
interdite). À lancer par Ismaël, APRÈS `scripts/verify_broker_readonly.py`,
un jour où les marchés concernés sont OUVERTS (CHFJPY et GBPUSD sont fermés
le week-end, BTCUSD/ETHUSD cotent 7 j/7).

Ce que fait le script, une position à la fois :
1. vérifie PAR CODE que l'environnement est DÉMO, sinon arrêt ;
2. relit la base : le trade doit être `ouvert`, source `hypothesis2_v2`,
   étiqueté `tp1_cloture_totale_broker` (combo retiré) — sinon il est sauté ;
3. vérifie côté broker que la position existe (GET /positions) ;
4. la ferme avec la fonction EXISTANTE `CapitalClient.close_position`
   (fermeture totale, même chemin que /stop_urgence) ;
5. vérifie qu'elle a disparu des positions broker, puis attend que
   l'exécuteur H2 (en marche) la réconcilie en base au prix réel
   (`reconcile_ghost_positions` → /history/activity) — jusqu'à 5 minutes ;
6. journal horodaté de chaque étape.

Aucun plafond, aucun stop, aucun autre trade n'est touché. Par défaut :
simulation (aucune clôture) ; `--apply` pour fermer réellement.

Usage (VPS, racine du dépôt) :
    venv/bin/python scripts/close_retired_combo_positions.py            # simulation
    venv/bin/python scripts/close_retired_combo_positions.py --apply    # clôture réelle (démo)
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
TRADE_IDS = (14877, 15948, 15954, 16329)
RECONCILE_TIMEOUT_SECONDS = 300
SPACING_SECONDS = 2.0


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)


def eligible(row) -> bool:
    """Garde-fou pur : seul un trade du combo retiré encore ouvert est fermé."""
    return (
        row is not None and row["statut"] == "ouvert" and row["source"] == "hypothesis2_v2"
        and row["anomalie_technique"] == "tp1_cloture_totale_broker" and bool(row["deal_id"])
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = load_config()
    if config.capital_environment != "demo":
        log(f"ARRÊT : CAPITAL_ENVIRONMENT={config.capital_environment!r}, pas 'demo'. Rien n'est fait.")
        return 3

    db = sqlite3.connect(config.db_path)
    db.row_factory = sqlite3.Row
    client = CapitalClient(config.capital_api_key_hypothesis2, config.capital_identifier_hypothesis2,
                           config.capital_api_password_hypothesis2, DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id_hypothesis2)
    log("Session ouverte sur le compte démo H2")

    for trade_id in TRADE_IDS:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not eligible(row):
            log(f"trade {trade_id} : non éligible (statut={row['statut'] if row else 'absent'}) — sauté")
            continue
        time.sleep(SPACING_SECONDS)
        open_ids = {p.get("position", {}).get("dealId") for p in client.get_open_positions()}
        if row["deal_id"] not in open_ids:
            log(f"trade {trade_id} {row['actif']} : position {row['deal_id']} ABSENTE chez le broker — rien à fermer, "
                "la réconciliation de l'exécuteur la traitera")
            continue
        if not args.apply:
            log(f"[simulation] trade {trade_id} {row['actif']} : position {row['deal_id']} ouverte — serait fermée")
            continue
        log(f"trade {trade_id} {row['actif']} : clôture de {row['deal_id']}")
        result = client.close_position(row["deal_id"], requested_at=datetime.now(timezone.utc).isoformat())
        log(f"trade {trade_id} : réponse broker niveau={result.get('level')} exécuté={result.get('executed_at')}")
        time.sleep(SPACING_SECONDS)
        still_open = row["deal_id"] in {p.get("position", {}).get("dealId") for p in client.get_open_positions()}
        log(f"trade {trade_id} : {'ENCORE OUVERTE — arrêt, vérifier à la main' if still_open else 'fermée chez le broker'}")
        if still_open:
            return 1
        deadline = time.monotonic() + RECONCILE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status = db.execute("SELECT statut, r_multiple_total FROM trades WHERE id = ?", (trade_id,)).fetchone()
            if status["statut"] != "ouvert":
                log(f"trade {trade_id} : réconcilié en base (statut={status['statut']}, R={status['r_multiple_total']})")
                break
            time.sleep(15)
        else:
            log(f"trade {trade_id} : pas encore réconcilié après {RECONCILE_TIMEOUT_SECONDS} s — l'exécuteur H2 "
                "tourne-t-il ? La réconciliation se fera à son prochain cycle.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
