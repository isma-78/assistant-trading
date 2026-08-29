"""
financing_capture.py — Point 7 (29/08/2026, voir docs/DECISIONS.md) :
`backtest_engine.FINANCING_BPS_PER_DAY` est un taux plat, toujours un
coût, jamais un crédit, IDENTIQUE quel que soit l'actif ou le sens. Un
sondage en direct sur le compte démo H1 (`GET /history/transactions`,
lecture seule, 29/08/2026) a trouvé six lignes `transactionType='SWAP'`
la même nuit, avec des SIGNES DIFFÉRENTS selon l'instrument (ex.
USDJPY=+0.23€, GBPUSD=-0.25€) — la réalité Capital.com est déjà
asymétrique, le modèle plat ne l'est pas. Aucune capture n'existait
avant ce module (vérifié par recherche dans tout `src/` avant d'écrire
ce fichier) — ce module l'ajoute, comme demandé, AVANT toute mesure ou
tout remplacement de la constante.

Fenêtre glissante de 24h CÔTÉ BROKER (`lastPeriod` plafonné à 86400
secondes, vérifié empiriquement — 172800 renvoie
`error.invalid.lastPeriod`) : une capture qui saute un jour PERD ces
transactions définitivement, aucun rattrapage possible auprès du broker
au-delà. Doit tourner au moins une fois par jour (cron), même
convention que `scripts/backup_and_sync.sh`.

Deux couches, même convention que le reste du projet :
- Calcul pur (`parse_swap_transactions`) : 100% couvert.
- Orchestration I/O (`capture_recent_financing`) : appel broker (lecture
  seule, aucun ordre) + écriture DB idempotente (INSERT OR IGNORE sur
  `reference`, jamais de doublon même si la capture tourne deux fois
  sur la même fenêtre).
"""

from typing import Dict, List

from src.capital_client import CapitalClient
from src.db import connection_scope

# Plafond empirique (29/08/2026) — au-delà, l'API renvoie
# error.invalid.lastPeriod. Jamais dépassé, jamais supposé plus large.
MAX_LAST_PERIOD_SECONDS = 86400


def parse_swap_transactions(raw_transactions: List[dict]) -> List[Dict[str, object]]:
    """Filtre `transactionType == 'SWAP'` (les autres types — dépôts,
    trades — ne concernent pas ce module) et normalise les champs
    utiles. `size` arrive en chaîne depuis l'API Capital.com — converti
    en float ici, une seule fois, jamais reconverti plus loin dans le
    projet."""
    result = []
    for raw in raw_transactions:
        if raw.get("transactionType") != "SWAP":
            continue
        result.append({
            "reference": raw["reference"],
            "instrument": raw["instrumentName"],
            "size_eur": float(raw["size"]),
            "date_utc": raw["dateUtc"],
        })
    return result


def capture_recent_financing(client: CapitalClient, db_path: str, now_iso: str) -> int:
    """Récupère les transactions SWAP des dernières 24h (plafond broker)
    et les persiste (idempotent). Retourne le nombre de lignes
    RÉELLEMENT insérées (les doublons déjà capturés ne comptent pas —
    permet de détecter un jour où la capture n'a rien trouvé de
    NOUVEAU, distinct d'un jour où l'appel API a échoué)."""
    raw = client.get(f"/history/transactions?lastPeriod={MAX_LAST_PERIOD_SECONDS}")
    rows = parse_swap_transactions(raw.get("transactions", []))

    inserted = 0
    with connection_scope(db_path) as conn:
        for row in rows:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO financing_transactions "
                "(reference, instrument, size_eur, date_utc, captured_at) "
                "VALUES (:reference, :instrument, :size_eur, :date_utc, :captured_at)",
                {**row, "captured_at": now_iso},
            )
            inserted += cursor.rowcount
    return inserted
