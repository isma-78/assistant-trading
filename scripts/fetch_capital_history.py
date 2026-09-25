"""
fetch_capital_history.py — Volet B de la mission du 24/09/2026 (voir
docs/DECISIONS.md) : récupère l'historique RÉEL des positions fermées
côté Capital.com depuis le 30/08/2026, pour les 5 comptes démo du
projet (Station X + Hypothèse #1 partagent le compte de base, H2/H3/H4/H5
ont chacun leur compte dédié — voir .env.example).

Script ponctuel, LECTURE SEULE (GET /history/activity uniquement, aucun
ordre, aucune modification), sur le même modèle que
discover_instruments.py/calibrate_pip_value.py déjà présents dans le
projet. N'écrit RIEN dans la base sqlite ni dans les tables trades/
trade_partials — sauvegarde uniquement la réponse BRUTE de l'API dans
data/capital_history_raw/, pour une inspection manuelle du schéma réel
AVANT d'écrire toute logique de rapprochement (B2) : la documentation
publique de l'API ne précise pas le détail exact du sous-objet `details`
d'une activité de type POSITION (voir docs/DECISIONS.md, audit du
24/09/2026) — jamais une hypothèse sur un nom de champ sans l'avoir vu
dans une vraie réponse.

Contrainte API confirmée : la fenêtre from/to est plafonnée à 1 jour par
appel (`GET /history/activity`) — ce script boucle donc jour par jour,
avec une pause entre chaque appel (même discipline que
retry_with_backoff déjà utilisé ailleurs dans le projet pour ce broker,
voir src/retry.py) pour ne jamais aggraver le risque de 429 documenté
sur ce compte démo partagé par 8 process.

Usage :
    python scripts/fetch_capital_history.py [--from 2026-08-30] [--to 2026-09-25]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient
from src.config import load_config
from src.retry import retry_with_backoff

_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
_OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "capital_history_raw"
_REQUEST_PAUSE_SECONDS = 1.5  # espace les appels, jamais de rafale


def _accounts(config):
    return [
        ("stationx_h1", config.capital_api_key, config.capital_identifier,
         config.capital_api_password, config.capital_account_id),
        ("hypothesis2", config.capital_api_key_hypothesis2, config.capital_identifier_hypothesis2,
         config.capital_api_password_hypothesis2, config.capital_account_id_hypothesis2),
        ("hypothesis3", config.capital_api_key_hypothesis3, config.capital_identifier_hypothesis3,
         config.capital_api_password_hypothesis3, config.capital_account_id_hypothesis3),
        ("hypothesis4", config.capital_api_key_hypothesis4, config.capital_identifier_hypothesis4,
         config.capital_api_password_hypothesis4, config.capital_account_id_hypothesis4),
        ("hypothesis5", config.capital_api_key_hypothesis5, config.capital_identifier_hypothesis5,
         config.capital_api_password_hypothesis5, config.capital_account_id_hypothesis5),
    ]


def _daterange(start: datetime, end: datetime):
    current = start
    while current < end:
        window_end = min(current + timedelta(days=1), end)
        yield current, window_end
        current = window_end


def fetch_account_history(label: str, api_key, identifier, password, account_id, start: datetime, end: datetime) -> int:
    if not all([api_key, identifier, password, account_id]):
        print(f"[{label}] identifiants incomplets dans .env — ignoré")
        return 0

    out_dir = _OUT_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)

    client = CapitalClient(api_key, identifier, password, _DEMO_BASE_URL)
    client.login()
    client.switch_account(account_id)
    print(f"[{label}] authentifié, compte {account_id} ciblé")

    total_items = 0
    for window_start, window_end in _daterange(start, end):
        day_label = window_start.strftime("%Y-%m-%d")
        out_file = out_dir / f"{day_label}.json"
        if out_file.exists():
            with open(out_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            total_items += len(cached.get("activities", []))
            continue

        params = {
            "from": window_start.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": window_end.strftime("%Y-%m-%dT%H:%M:%S"),
            "detailed": "true",
        }
        try:
            data = retry_with_backoff(
                lambda: client.get("/history/activity", params=params),
                exceptions=(CapitalApiError,),
            )
        except CapitalApiError as exc:
            print(f"[{label}] {day_label} : échec après réessais — {exc}")
            data = {"activities": [], "_error": str(exc)}

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        n = len(data.get("activities", []))
        total_items += n
        print(f"[{label}] {day_label} : {n} activité(s)")
        time.sleep(_REQUEST_PAUSE_SECONDS)

    print(f"[{label}] TOTAL : {total_items} activité(s) sur {(end - start).days} jours")
    return total_items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", default="2026-08-30")
    parser.add_argument("--to", dest="date_to", default=None)
    args = parser.parse_args()

    config = load_config()
    if config.capital_environment != "demo":
        print("REFUS : CAPITAL_ENVIRONMENT n'est pas 'demo' — ce script ne s'exécute jamais hors démo.")
        sys.exit(1)

    start = datetime.strptime(args.date_from, "%Y-%m-%d")
    end = datetime.strptime(args.date_to, "%Y-%m-%d") if args.date_to else datetime.now(timezone.utc).replace(tzinfo=None)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    grand_total = 0
    for label, api_key, identifier, password, account_id in _accounts(config):
        grand_total += fetch_account_history(label, api_key, identifier, password, account_id, start, end)

    print(f"\nTOTAL GÉNÉRAL : {grand_total} activités sur les 5 comptes, {start.date()} -> {end.date()}")
    print(f"Fichiers bruts sauvegardés dans {_OUT_DIR}")


if __name__ == "__main__":
    main()
