"""
_couples_live_08-10.py — Étape 4 de la Partie 3 (08/10/2026),
docs/PROTOCOLE_COUPLES_08-10.md §7 (« les trades live servent à
décrire, jamais à tester »). Par couple (hypothèse, actif) : n live
(trades fermés, réconciliés, ouverts depuis le redémarrage corrigé du
25/09 19:35 UTC) et date estimée de n=30 (projection linéaire, ordre de
grandeur). AUCUN verdict, AUCUNE sélection.

Lecture seule d'un instantané LOCAL de la base (le plus frais
disponible dans ce dépôt, téléchargé en lecture seule depuis le VPS
pendant la Partie 2 de ce mandat — pas d'accès VPS dans CETTE Partie 3,
conformément à ses limites). CHFJPY exclue (cohérent avec le reste du
protocole).
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "snapshots"
DB_PATH = ROOT / "data" / "assistant_trading_vps_snapshot_fresh.db"  # Partie 2, téléchargé 2026-10-07T19:41 UTC

POPULATION_START = "2026-09-25T19:35:00"
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]
SOURCE_BY_HYP = {
    "H1": "hypothesis_v2", "H2": "hypothesis2_v2", "H3": "hypothesis3_v2",
    "H4": "hypothesis4_v2", "H5": "hypothesis5_v2",
}
N_TARGET = 30


def estimate_date_n_target(n: int, first_date: str, last_date: str, now: datetime, target: int = N_TARGET):
    if n >= target or n < 2 or not first_date or not last_date:
        return None
    start = datetime.fromisoformat(first_date[:19])
    end = datetime.fromisoformat(last_date[:19])
    span_days = (end - start).total_seconds() / 86400
    if span_days <= 0:
        return None
    rate_per_day = (n - 1) / span_days
    days_needed = (target - n) / rate_per_day
    return (now + timedelta(days=days_needed)).date().isoformat()


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now()
    results = {}

    print(f"Base utilisée : {DB_PATH.name} (instantané lecture seule, Partie 2 de ce mandat)")
    for hyp_key, source in SOURCE_BY_HYP.items():
        results[hyp_key] = {}
        print(f"\n===== {hyp_key} ({source}) =====")
        for asset in ASSETS:
            rows = conn.execute(
                "SELECT ouvert_at FROM trades WHERE source = ? AND actif = ? AND statut = 'ferme' "
                "AND r_multiple_total IS NOT NULL AND ouvert_at >= ? ORDER BY ouvert_at",
                (source, asset, POPULATION_START),
            ).fetchall()
            n = len(rows)
            first_date = rows[0]["ouvert_at"] if rows else None
            last_date = rows[-1]["ouvert_at"] if rows else None
            date_n30 = estimate_date_n_target(n, first_date, last_date, now)
            results[hyp_key][asset] = {"n_live": n, "date_n30_estimee": date_n30}
            print(f"  {asset}: n_live={n}" + (f" date_n=30 estimée={date_n30}" if date_n30 else ""))

    conn.close()
    (OUT / "couples_live_08-10.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
