"""
backfill_financing.py — A4 du bilan du 05/10/2026 : rattrapage idempotent
des transactions SWAP (financement réel) manquées pendant la panne du cron
`capture_financing.py` (ModuleNotFoundError chaque nuit depuis le
30/08/2026).

Lecture seule côté broker (`GET /history/transactions?from=&to=`), une
fenêtre d'un jour par appel, pause de 1,5 s entre appels, ARRÊT immédiat
au premier 429. Écriture limitée à `financing_transactions` (INSERT OR
IGNORE sur `reference`) : relancer le script ne crée jamais de doublon.
Compte principal uniquement, comme le cron (voir docstring de
capture_financing.py).

Si l'API refuse `from`/`to` sur cet endpoint, le script s'arrête au premier
refus et le dit : le rattrapage est alors impossible (seules les dernières
24 h restent accessibles via `lastPeriod`).

Usage (VPS, racine du dépôt, à lancer par Ismaël) :
    venv/bin/python scripts/backfill_financing.py [--from 2026-08-30] [--to 2026-10-10]
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402
from src.db import init_db  # noqa: E402
from src.financing_capture import capture_financing_window  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
PAUSE_SECONDS = 1.5


def day_windows(start: datetime, end: datetime):
    current = start
    while current < end:
        window_end = min(current + timedelta(days=1), end)
        yield current, window_end
        current = window_end


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", default="2026-08-30")
    parser.add_argument("--to", dest="date_to", default=None)
    args = parser.parse_args()

    config = load_config()
    if config.capital_environment != "demo":
        print("REFUS : CAPITAL_ENVIRONMENT n'est pas 'demo' — aucun appel broker.")
        return 3
    init_db(config.db_path)
    start = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc) if args.date_to else datetime.now(timezone.utc)

    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id)

    total = 0
    for window_start, window_end in day_windows(start, end):
        try:
            inserted = capture_financing_window(
                client, config.db_path,
                window_start.strftime("%Y-%m-%dT%H:%M:%S"), window_end.strftime("%Y-%m-%dT%H:%M:%S"),
                datetime.now(timezone.utc).isoformat(),
            )
        except CapitalApiError as exc:
            if "429" in str(exc) or "too-many" in str(exc):
                print(f"429 au {window_start:%Y-%m-%d} — ARRÊT, relancer plus tard (idempotent). Total inséré : {total}")
                return 2
            print(f"Refus de l'API au {window_start:%Y-%m-%d} : {exc} — ARRÊT. Total inséré : {total}")
            return 1
        total += inserted
        print(f"{window_start:%Y-%m-%d} : {inserted} nouvelle(s) transaction(s) SWAP")
        time.sleep(PAUSE_SECONDS)
    print(f"Terminé : {total} transaction(s) SWAP ajoutée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
