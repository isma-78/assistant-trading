"""
refresh_historical_tail.py — Complète data/historical/{EPIC}_{RES}.json
avec les seules bougies postérieures à la dernière déjà présente (25/09/2026,
voir docs/DECISIONS.md, réparation du script de fidélité : les fichiers
s'arrêtaient au 28-30/08/2026, avant toute la fenêtre live).

Contraintes appliquées (décision d'Ismaël du 25/09/2026) :
- même échelonnement que le Volet B (`fetch_capital_history.py`) : une
  requête à la fois, pause de 1,5 s, `retry_with_backoff` ;
- ARRÊT IMMÉDIAT au premier signe de 429 : sur nos propres requêtes, OU
  côté exécuteurs live (compteur `api_error_streak:*` > 0 mis à jour depuis
  le démarrage de ce script, ou un placement annulé pour `rate_limit_429`
  depuis ce démarrage) — les exécuteurs passent toujours avant ce script.

Jamais de bougie en formation : une bougie n'est ajoutée que si elle est
close (début + durée <= maintenant). Écriture atomique (.tmp puis rename) :
un arrêt en cours de route laisse le fichier précédent intact.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient
from src.config import load_config
from src.retry import retry_with_backoff

_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
HISTORICAL_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
RESOLUTIONS = {"HOUR": timedelta(hours=1), "HOUR_4": timedelta(hours=4), "DAY": timedelta(days=1)}
MAX_BARS_PER_REQUEST = 1000
PAUSE_SECONDS = 1.5
_RATE_LIMIT_MARKERS = ("too-many.requests", "429")


class RateLimitAbort(Exception):
    pass


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", ""))


def executors_rate_limited(db_path: str, since_iso: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        streak = conn.execute(
            "SELECT key, value FROM system_state WHERE key LIKE 'api_error_streak:%' "
            "AND updated_at >= ? AND CAST(value AS INTEGER) > 0",
            (since_iso,),
        ).fetchall()
        rejected = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE annulation_motif = 'rate_limit_429' AND ouvert_at >= ?",
            (since_iso,),
        ).fetchone()[0]
    finally:
        conn.close()
    if streak:
        return f"compteur d'erreurs API > 0 côté exécuteurs : {streak}"
    if rejected:
        return f"{rejected} placement(s) annulé(s) pour 429 côté exécuteurs"
    return ""


def fetch_tail(client, epic: str, resolution: str, last_time: datetime, now: datetime) -> list:
    step = RESOLUTIONS[resolution]
    new_points, cursor = [], last_time
    while cursor < now:
        window_end = min(cursor + step * MAX_BARS_PER_REQUEST, now)
        params = {
            "resolution": resolution, "max": MAX_BARS_PER_REQUEST,
            "from": cursor.strftime("%Y-%m-%dT%H:%M:%S"), "to": window_end.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            resp = retry_with_backoff(
                lambda p=params: client.get(f"/prices/{epic}", params=p),
                exceptions=(CapitalApiError, requests.exceptions.RequestException),
            )
        except CapitalApiError as exc:
            if any(m in str(exc) for m in _RATE_LIMIT_MARKERS):
                raise RateLimitAbort(f"429 sur notre propre requête {epic}/{resolution} : {exc}")
            if "error.prices.not-found" in str(exc):
                break
            raise
        new_points.extend(resp.get("prices", []))
        cursor = window_end
        time.sleep(PAUSE_SECONDS)
    return new_points


def merge(existing: list, fresh: list, step: timedelta, now: datetime) -> list:
    by_time = {p.get("snapshotTimeUTC"): p for p in existing}
    for point in fresh:
        ts = point.get("snapshotTimeUTC")
        if ts and _parse(ts) + step <= now:  # jamais une bougie encore en formation
            by_time[ts] = point
    return [by_time[k] for k in sorted(by_time)]


def main() -> None:
    config = load_config()
    started = datetime.now(timezone.utc)
    started_iso = started.isoformat()
    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, _DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id)
    now = started.replace(tzinfo=None)

    for resolution, step in RESOLUTIONS.items():
        for epic in ASSETS:
            reason = executors_rate_limited(config.db_path, started_iso)
            if reason:
                print(f"ARRÊT IMMÉDIAT : {reason} — fichiers déjà traités conservés, les autres intacts.")
                sys.exit(2)
            path = HISTORICAL_DIR / f"{epic}_{resolution}.json"
            existing = json.loads(path.read_text(encoding="utf-8"))
            last = max(_parse(p["snapshotTimeUTC"]) for p in existing if p.get("snapshotTimeUTC"))
            try:
                fresh = fetch_tail(client, epic, resolution, last, now)
            except RateLimitAbort as exc:
                print(f"ARRÊT IMMÉDIAT : {exc}")
                sys.exit(2)
            merged = merge(existing, fresh, step, now)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(merged), encoding="utf-8")
            tmp.replace(path)
            print(f"{epic}/{resolution} : {len(existing)} -> {len(merged)} bougies, dernière {merged[-1]['snapshotTimeUTC']}")
    print("Terminé.")


if __name__ == "__main__":
    main()
