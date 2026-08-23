"""
retrofit_h2_h3_tp_partiel.py — Application RÉTROACTIVE, ponctuelle, de la
sortie à prise de profit (TP1 1R / TP2 2R / reliquat trailing, §2.10) aux
trades H2/H3 ENCORE OUVERTS au moment de la révision de la couche
session/multi-timeframe du 23/08/2026 (voir docs/DECISIONS.md,
docs/HYPOTHESES.md) — décision explicite d'Ismaël, écart assumé par
rapport à la discipline "prospectif uniquement" suivie plus tôt le même
jour pour la bascule initiale de H2/H3 (`hypothesis2_strategy.py`/
`hypothesis3_strategy.py`).

Ne touche JAMAIS :
- un trade déjà clôturé (statut != 'ouvert') ;
- un trade dont exit_type != 'trailing_pur' (déjà passé par la bascule
  prospective du même jour, ou source hors périmètre H2/H3).

TP1/TP2 calculés à partir de l'entrée et du stop INITIAL déjà
enregistrés pour chaque trade (`prix_entree_reel` ou, à défaut,
`prix_entree_prevu` ; `stop_loss_initial`, jamais le stop courant déjà
resserré) — PAS recalculés selon l'évolution du trade depuis son
ouverture, mêmes constantes que hypothesis2_strategy.py/
hypothesis3_strategy.py (TP1_R_MULTIPLE=1.0, TP2_R_MULTIPLE=2.0). Écrit
`signals.tp1`/`tp2` sur le signal d'origine du trade (relu à chaque
cycle par `executor._load_open_trade_state` — la bascule prend donc
effet dès le prochain cycle de l'exécuteur concerné, aucun redémarrage
requis) et `trades.exit_type = 'tp_partiel_retroactif'` (jamais fusionné
avec 'trailing_pur' ni avec 'tp_partiel' classique — dimension
distincte, traçable en base).

Script à lancer UNE SEULE FOIS, manuellement, sous supervision — jamais
un backfill automatique au démarrage (contrairement aux `db._backfill_*`
qui ne touchent que des trades déjà CLÔTURÉS) : celui-ci modifie le
comportement de gestion de positions RÉELLEMENT OUVERTES, trop
conséquent pour tourner sans revue explicite à chaque redémarrage.
Idempotent par construction (la condition `exit_type = 'trailing_pur'`
exclut tout trade déjà converti) mais pensé pour un usage ponctuel, pas
un cron.

Usage :
    python -m scripts.retrofit_h2_h3_tp_partiel [--db-path CHEMIN] [--dry-run]

--dry-run : affiche ce qui serait fait (id, ancien/nouveau exit_type,
TP1/TP2 calculés) sans écrire en base.
"""

import argparse

from src.db import connection_scope
from src.executor import HYPOTHESIS2_SOURCE, HYPOTHESIS3_SOURCE
from src.trend_strategy import compute_tp_levels

TP1_R_MULTIPLE = 1.0
TP2_R_MULTIPLE = 2.0

_TARGET_SOURCES = (HYPOTHESIS2_SOURCE, HYPOTHESIS3_SOURCE)


def find_candidate_trades(db_path: str):
    """Trades H2/H3 encore ouverts, toujours en trailing_pur (jamais
    passés par la bascule prospective du 23/08/2026 matin)."""
    with connection_scope(db_path) as conn:
        placeholders = ",".join("?" for _ in _TARGET_SOURCES)
        return conn.execute(
            f"SELECT * FROM trades WHERE statut = 'ouvert' AND exit_type = 'trailing_pur' "
            f"AND source IN ({placeholders})",
            _TARGET_SOURCES,
        ).fetchall()


def _compute_tp1_tp2(trade_row):
    entry_price = trade_row["prix_entree_reel"] or trade_row["prix_entree_prevu"]
    stop_price = trade_row["stop_loss_initial"]
    return compute_tp_levels(trade_row["direction"], entry_price, stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE)


def retrofit_trade(conn, trade_row) -> dict:
    """Écrit tp1/tp2 sur le signal d'origine et bascule trades.exit_type
    — une seule transaction (via `conn`, fourni par l'appelant, jamais
    ouverte ici) pour que les deux écritures soient toujours cohérentes
    entre elles."""
    tp1, tp2 = _compute_tp1_tp2(trade_row)
    conn.execute("UPDATE signals SET tp1 = ?, tp2 = ? WHERE id = ?", (tp1, tp2, trade_row["signal_id"]))
    conn.execute("UPDATE trades SET exit_type = 'tp_partiel_retroactif' WHERE id = ?", (trade_row["id"],))
    return {
        "trade_id": trade_row["id"], "deal_id": trade_row["deal_id"],
        "source": trade_row["source"], "actif": trade_row["actif"],
        "ancien_exit_type": "trailing_pur", "nouveau_exit_type": "tp_partiel_retroactif",
        "tp1": tp1, "tp2": tp2,
    }


def run(db_path: str, dry_run: bool = False) -> list:
    candidates = find_candidate_trades(db_path)
    if not candidates:
        print("Aucun trade H2/H3 ouvert en trailing_pur — rien à faire.")
        return []

    report = []
    if dry_run:
        for trade_row in candidates:
            tp1, tp2 = _compute_tp1_tp2(trade_row)
            report.append({
                "trade_id": trade_row["id"], "deal_id": trade_row["deal_id"],
                "source": trade_row["source"], "actif": trade_row["actif"],
                "ancien_exit_type": "trailing_pur", "nouveau_exit_type": "tp_partiel_retroactif (dry-run)",
                "tp1": tp1, "tp2": tp2,
            })
    else:
        with connection_scope(db_path) as conn:
            for trade_row in candidates:
                report.append(retrofit_trade(conn, trade_row))

    for entry in report:
        print(
            f"trade_id={entry['trade_id']} deal_id={entry['deal_id']} "
            f"source={entry['source']} actif={entry['actif']} : "
            f"{entry['ancien_exit_type']} -> {entry['nouveau_exit_type']}, "
            f"tp1={entry['tp1']}, tp2={entry['tp2']}"
        )
    return report


if __name__ == "__main__":
    from src.config import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="Par défaut : config.db_path (.env)")
    parser.add_argument("--dry-run", action="store_true", help="N'écrit rien, affiche seulement ce qui serait fait")
    args = parser.parse_args()

    resolved_db_path = args.db_path or load_config().db_path
    run(resolved_db_path, dry_run=args.dry_run)
