"""
rapport_couples_forward.py — Rapport hebdomadaire (lundi) du suivi
forward de TOUS les couples (hypothèse _v2 x actif de la liste
blanche), Partie 4 du 08/10/2026 (`docs/COUPLES_V2_08-10.md` étape 5).

**À activer seulement après `scripts/run_shadow_couples_cycle.py`**
(lui-même non branché au déploiement, voir
`docs/CHECKLIST_10-10_UNIQUE.md`). Lecture seule de la base de
production. AUCUN appel broker, AUCUNE action sur un exécuteur ou une
configuration — écrit uniquement `docs/SUIVI_COUPLES/AAAA-MM-JJ.md`.
Aucune action automatique, aucun verdict, aucune sélection.

Cron prévu (lundi matin, à installer après activation) :
    0 6 * * 1 cd /home/assistant/assistant-trading && venv/bin/python scripts/rapport_couples_forward.py >> logs/rapport_couples_forward_cron.log 2>&1
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.couples_forward_report import (  # noqa: E402
    build_couple_report,
    cluster_rejection_rates,
    format_markdown,
)
from src.evolution_v2_test import TradeRef  # noqa: E402
from src.shadow_tracking import SHADOW_COUPLES  # noqa: E402
from src.simulator_fidelity import PortfolioTrade  # noqa: E402

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "SUIVI_COUPLES")
PROVISIONAL_RISK_EUR = 20.2  # même ordre que executor.open_signal (taux boosté x enveloppe), voir docs A5/A8

BASELINE_SOURCE = {source: source.replace("_shadow_couples", "") for source in SHADOW_COUPLES}


def _trade_refs(rows):
    return [TradeRef(r["actif"], r["direction"], r["ouvert_at"], r["r_multiple_total"]) for r in rows]


def load_shadow_couple(conn, source, asset):
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, ferme_at, r_multiple_total FROM shadow_trades "
        "WHERE source = ? AND actif = ? AND statut = 'ferme'", (source, asset),
    ).fetchall()
    return _trade_refs(rows), rows


def load_live_couple(conn, source, asset):
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, r_multiple_total FROM trades "
        "WHERE source = ? AND actif = ? AND statut = 'ferme' AND anomalie_technique IS NULL "
        "AND r_multiple_total IS NOT NULL", (source, asset),
    ).fetchall()
    return _trade_refs(rows)


def main() -> int:
    from src.config import load_config

    config = load_config()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    try:
        pooled_for_cluster = []
        raw_by_couple = {}
        for shadow_source, cfg in SHADOW_COUPLES.items():
            live_source = BASELINE_SOURCE[shadow_source]
            for asset in cfg["assets"]:
                _, raw_rows = load_shadow_couple(conn, shadow_source, asset)
                raw_by_couple[(shadow_source, asset)] = raw_rows
                for row in raw_rows:
                    pooled_for_cluster.append(PortfolioTrade(
                        shadow_source, asset, row["ouvert_at"], row["ferme_at"] or row["ouvert_at"],
                        PROVISIONAL_RISK_EUR, row["r_multiple_total"],
                    ))

        rejection_rates = cluster_rejection_rates(pooled_for_cluster, PROVISIONAL_RISK_EUR)

        reports = []
        for shadow_source, cfg in SHADOW_COUPLES.items():
            live_source = BASELINE_SOURCE[shadow_source]
            for asset in cfg["assets"]:
                shadow_refs = _trade_refs(raw_by_couple[(shadow_source, asset)])
                live_refs = load_live_couple(conn, live_source, asset)
                rate = rejection_rates.get((shadow_source, asset))
                reports.append(build_couple_report(live_source, asset, shadow_refs, live_refs, rate))

        date_iso = now.date().isoformat()
        markdown = format_markdown(reports, date_iso)
        os.makedirs(REPORT_DIR, exist_ok=True)
        report_path = os.path.join(REPORT_DIR, f"{date_iso}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"Rapport écrit : {report_path} ({len(reports)} couples).")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
