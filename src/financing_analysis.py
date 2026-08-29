"""
financing_analysis.py — Point 7 (29/08/2026, voir docs/DECISIONS.md) :
agrège le financement réel capturé par `financing_capture.py` par
(actif, direction), pour comparer au taux plat `backtest_engine.
FINANCING_BPS_PER_DAY`.

`transactionType='SWAP'` ne porte pas le sens (long/short) du compte au
moment du débit — reconstruit ici en cherchant, dans `trades`, le trade
ouvert sur cet actif à cet instant précis (`ouvert_at <= t <= ferme_at`,
ou encore ouvert). `None` (jamais deviné) si 0 ou PLUSIEURS sens
différents étaient ouverts au même instant sur le même actif (plusieurs
hypothèses peuvent trader le même actif en sens opposés — voir
docs/DECISIONS.md, scope (actif, source) de la contrainte anti-doublon).

Couche pure (`resolve_direction_at_time`, `aggregate_financing_rows`) :
100% couverte. Orchestration (`aggregate_financing_by_asset_direction`) :
lecture DB seule.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.db import connection_scope


def resolve_direction_at_time(db_path: str, actif: str, timestamp_utc: str) -> Optional[str]:
    """`timestamp_utc` : format `dateUtc` de Capital.com (naïf, déjà
    UTC par construction du champ). Comparé à `trades.ouvert_at`/
    `ferme_at` (parfois `Z`, parfois `+00:00` + microsecondes — même
    hétérogénéité que le bug du point 4) via `datetime.fromisoformat`,
    dépouillé de `tzinfo` pour rester comparable à un horodatage broker
    naïf (les deux représentent déjà un instant UTC, jamais une
    conversion de fuseau)."""
    target = datetime.fromisoformat(timestamp_utc).replace(tzinfo=None)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT direction, ouvert_at, ferme_at FROM trades WHERE actif = ? "
            "AND statut IN ('ouvert', 'ferme')",
            (actif,),
        ).fetchall()

    directions = set()
    for row in rows:
        opened = datetime.fromisoformat(row["ouvert_at"]).replace(tzinfo=None)
        if opened > target:
            continue
        if row["ferme_at"] is not None:
            closed = datetime.fromisoformat(row["ferme_at"]).replace(tzinfo=None)
            if closed < target:
                continue
        directions.add(row["direction"])

    if len(directions) == 1:
        return next(iter(directions))
    return None


def aggregate_financing_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Calcul pur : `rows` = [{"instrument", "size_eur", "direction"}, ...]
    (direction déjà résolue par l'appelant, `None` accepté et regroupé à
    part sous `"indetermine"` — jamais fusionné avec un sens réel)."""
    groups: Dict[Tuple[str, str], List[float]] = {}
    for row in rows:
        key = (row["instrument"], row["direction"] or "indetermine")
        groups.setdefault(key, []).append(row["size_eur"])

    return [
        {
            "actif": actif, "direction": direction, "n": len(values),
            "financing_moyen_eur": sum(values) / len(values),
        }
        for (actif, direction), values in sorted(groups.items())
    ]


def aggregate_financing_by_asset_direction(db_path: str) -> List[Dict[str, object]]:
    """Orchestration : lit `financing_transactions`, résout la direction
    de chaque ligne, agrège. Coûteux en requêtes (une par ligne de
    financement) mais le volume est structurellement faible (au plus
    quelques lignes par actif et par jour, jamais un flux à haute
    fréquence)."""
    with connection_scope(db_path) as conn:
        raw_rows = conn.execute(
            "SELECT instrument, size_eur, date_utc FROM financing_transactions"
        ).fetchall()

    enriched = [
        {
            "instrument": row["instrument"],
            "size_eur": row["size_eur"],
            "direction": resolve_direction_at_time(db_path, row["instrument"], row["date_utc"]),
        }
        for row in raw_rows
    ]
    return aggregate_financing_rows(enriched)
