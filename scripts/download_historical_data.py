"""
download_historical_data.py — Téléchargement en masse, PONCTUEL, de
l'historique Capital.com nécessaire au backtest rétrospectif (§2.11).
Pré-enregistré dans docs/HYPOTHESES.md (24/08/2026, soir) avant d'être
écrit — méthodologie complète (résolutions par hypothèse, throttle,
pagination) documentée là-bas, pas répétée ici en détail.

JAMAIS appelé depuis les 6 boucles live (executor_loop/trend_executor/
hypothesisN_executor) — script séparé, lancé manuellement. Persiste sur
disque (data/historical/{epic}_{resolution}.json, liste de points bruts
Capital.com — bid/ask conservés, format directement compatible avec
src.backtest_engine.bar_from_raw) : un calcul de backtest ultérieur ne
refait aucun appel réseau.

Résolutions nécessaires (voir docs/HYPOTHESES.md — aucune hypothèse
n'utilise deux résolutions différentes, H3/H4 réutilisent juste deux
instruments de plus à LA MÊME résolution que leur déclencheur) :
- HOUR : les 8 actifs de la liste blanche (Hypothèse #1)
- MINUTE_15 : les 8 mêmes actifs (Hypothèses #2, #3, #4, #5 — US30/US100
  déjà inclus dans les 8, aucun instrument supplémentaire à télécharger)

Pagination : fenêtres de 1000 bougies (plafond dur mesuré empiriquement
le 24/08/2026, voir docs/HYPOTHESES.md), en remontant depuis maintenant
jusqu'au premier `error.prices.not-found` (limite réelle du compte démo,
jamais une valeur figée en dur) ou jusqu'à SAFETY_MAX_DAYS_BACK, ce qui
vient en premier. `src/retry.py` absorbe un 429 transitoire par page ;
THROTTLE_SECONDS (indépendant du retry) évite de le provoquer — mesuré
le 24/08/2026 : 429 atteint après 16 requêtes rapprochées sur ce compte,
THROTTLE_SECONDS reste très large en dessous.

Usage :
    python scripts/download_historical_data.py [--assets GOLD,EURUSD] [--resolutions HOUR,MINUTE_15]

Reprise : ré-exécuter le script retélécharge tout depuis le début pour
chaque (actif, résolution) demandé — pas de reprise incrémentale (script
ponctuel, voir docs/HYPOTHESES.md). Écrit sur disque après CHAQUE page
réussie (pas seulement à la fin) : une interruption ne perd que la page
en cours, jamais tout le travail déjà accompli pour cet (actif,
résolution).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.capital_client import CapitalApiError, CapitalClient
from src.config import load_config
from src.retry import retry_with_backoff

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "historical"
_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]
ALL_RESOLUTIONS = ["HOUR", "MINUTE_15"]

MAX_BARS_PER_REQUEST = 1000  # plafond dur mesuré le 24/08/2026 (error.invalid.max au-delà)
THROTTLE_SECONDS = 8.0       # très large sous le seuil de 429 mesuré (16 requêtes rapprochées)
SAFETY_MAX_DAYS_BACK = 800   # garde-fou, au-delà de la profondeur mesurée (~730j) — le broker s'arrête avant

# "HOUR_4" ajouté le 25/08/2026 (voir docs/HYPOTHESES.md, chantier de
# refonte H2-H5, Phase 2 branche A — test de résolution 4h) — résolution
# valide vérifiée empiriquement (error.invalid.resolution sur "HOUR4"/
# "MINUTE_240", "HOUR_4" accepté), jamais dans ALL_RESOLUTIONS par
# défaut (téléchargée explicitement via --resolutions HOUR_4 seulement
# quand nécessaire, pas à chaque exécution du script).
_RESOLUTION_MINUTES = {"HOUR": 60, "MINUTE_15": 15, "HOUR_4": 240}


def _page_window(to_time: datetime, resolution: str) -> tuple:
    # -1 bougie de marge de sécurité : une fenêtre de EXACTEMENT
    # `MAX_BARS_PER_REQUEST` bougies déclenche parfois
    # `error.invalid.max.daterange` (limite de plage par requête,
    # distincte du plafond `max` lui-même — trouvée empiriquement le
    # 24/08/2026 en lançant ce script, seuil mesuré à ~999,5-1000
    # bougies selon l'arrondi, voir docs/DECISIONS.md). Reste large
    # (1 bougie sur 1000, perte négligeable) plutôt que de retenter une
    # limite exacte fragile.
    minutes = _RESOLUTION_MINUTES[resolution] * (MAX_BARS_PER_REQUEST - 1)
    from_time = to_time - timedelta(minutes=minutes)
    return from_time, to_time


def download_one(client: CapitalClient, epic: str, resolution: str, now: datetime) -> list:
    """Télécharge tout l'historique disponible pour (epic, resolution),
    ordre chronologique (plus ancienne bougie en premier). Écrit
    progressivement sur disque après chaque page."""
    all_points: list = []
    to_time = now
    days_back = 0
    output_path = OUTPUT_DIR / f"{epic}_{resolution}.json"

    while days_back < SAFETY_MAX_DAYS_BACK:
        from_time, to_time_window = _page_window(to_time, resolution)
        params = {
            "resolution": resolution,
            # `max` DOIT être explicite : sans lui, l'API applique un
            # plafond implicite très bas (10 bougies observées) et rejette
            # `from`/`to` avec `error.invalid.max.daterange` dès que la
            # plage dépasse ce que ce plafond implicite justifierait —
            # trouvé empiriquement le 24/08/2026 en lançant ce script
            # (voir docs/DECISIONS.md), pas anticipé par la sonde initiale
            # (qui ne testait que des fenêtres d'un jour).
            "max": MAX_BARS_PER_REQUEST,
            "from": from_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": to_time_window.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            resp = retry_with_backoff(
                lambda p=params: client.get(f"/prices/{epic}", params=p),
                exceptions=(CapitalApiError, requests.exceptions.RequestException),
            )
        except CapitalApiError as exc:
            if "error.prices.not-found" in str(exc):
                print(f"  {epic}/{resolution} : limite d'historique atteinte à {from_time.date()} — arrêt.")
                break
            raise

        page = resp.get("prices", [])
        if not page:
            print(f"  {epic}/{resolution} : page vide à {from_time.date()} — arrêt.")
            break

        all_points = page + all_points  # page est chronologique ; on préfixe (on remonte dans le temps)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(all_points), encoding="utf-8")
        print(f"  {epic}/{resolution} : +{len(page)} bougies (total {len(all_points)}, jusqu'à {from_time.date()})")

        to_time = from_time
        days_back = (now - to_time).days
        time.sleep(THROTTLE_SECONDS)

    return all_points


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", default=",".join(ALL_ASSETS))
    parser.add_argument("--resolutions", default=",".join(ALL_RESOLUTIONS))
    args = parser.parse_args()

    assets = [a.strip() for a in args.assets.split(",") if a.strip()]
    resolutions = [r.strip() for r in args.resolutions.split(",") if r.strip()]

    config = load_config()
    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, _DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    print(f"Téléchargement historique démarré ({now.isoformat()}Z) — {len(assets)} actifs x {len(resolutions)} résolutions")

    for resolution in resolutions:
        for epic in assets:
            print(f"-- {epic} / {resolution} --")
            points = download_one(client, epic, resolution, now)
            print(f"  {epic}/{resolution} : {len(points)} bougies au total.")

    print("Terminé.")


if __name__ == "__main__":
    main()
