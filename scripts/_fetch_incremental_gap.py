"""
_fetch_incremental_gap.py — Script PONCTUEL, jamais commité comme partie
du pipeline (préfixe _ volontaire), pour combler le petit écart entre la
fin de l'historique déjà téléchargé (2026-08-24T17:30) et maintenant,
nécessaire pour comparer le backtest aux trades réels déjà passés
(demande d'Ismaël du 25/08/2026, voir docs/DECISIONS.md). Récupère
seulement les derniers bars via get_prices (pas de pagination complète,
écart < 1 jour), fusionne par horodatage dans les fichiers JSON
existants (dédoublonné), aucune donnée réécrite/perdue.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.capital_client import CapitalClient
from src.config import load_config

_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]
HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"


def main():
    config = load_config()
    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, _DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id)

    for asset in ASSETS:
        result = client.get_prices(asset, resolution="MINUTE_15", max_bars=300)
        new_points = result.get("prices", [])
        path = HISTORICAL_DIR / f"{asset}_MINUTE_15.json"
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_times = {p.get("snapshotTimeUTC") or p.get("snapshotTime") for p in existing}
        added = 0
        for p in new_points:
            t = p.get("snapshotTimeUTC") or p.get("snapshotTime")
            if t not in existing_times:
                existing.append(p)
                existing_times.add(t)
                added += 1
        existing.sort(key=lambda p: p.get("snapshotTimeUTC") or p.get("snapshotTime"))
        path.write_text(json.dumps(existing), encoding="utf-8")
        last = existing[-1]
        last_t = last.get("snapshotTimeUTC") or last.get("snapshotTime")
        print(f"{asset}: +{added} bougies, dernière = {last_t}, total = {len(existing)}")
        time.sleep(2.0)


if __name__ == "__main__":
    main()
