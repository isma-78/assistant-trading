"""
rapport_hebdo_shadow.py — Rapport hebdomadaire du suivi shadow des 4
candidates V2 (07/10/2026, étape 4 du mandat).

**À activer seulement après le shadow lui-même** (voir
docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md). Lecture seule de la base de
production. AUCUN appel broker, AUCUNE action sur un exécuteur ou une
configuration — écrit uniquement `docs/SUIVI_SHADOW/AAAA-MM-JJ.md` et
envoie un résumé Telegram.

Cron prévu (lundi matin, à installer après l'activation du shadow) :
    0 6 * * 1 cd /home/assistant/assistant-trading && venv/bin/python scripts/rapport_hebdo_shadow.py >> logs/rapport_hebdo_shadow_cron.log 2>&1
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.evolution_v2_test import TradeRef  # noqa: E402
from src.shadow_tracking import SHADOW_CANDIDATES  # noqa: E402
from src.shadow_weekly_report import (  # noqa: E402
    build_hypothesis_report,
    cluster_rejection_rate,
    format_weekly_markdown,
    format_weekly_telegram,
)
from src.simulator_fidelity import PortfolioTrade  # noqa: E402

EXPECTED_EFFECT = {"hypothesis_v3cand": 0.24, "hypothesis2_v3cand": 0.13, "hypothesis3_v3cand": 0.13,
                   "hypothesis4_v3cand": 0.10}
BASELINE_SOURCE = {"hypothesis_v3cand": "hypothesis_v2", "hypothesis2_v3cand": "hypothesis2_v2",
                   "hypothesis3_v3cand": "hypothesis3_v2", "hypothesis4_v3cand": "hypothesis4_v2"}
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "SUIVI_SHADOW")


def _trade_refs(rows, r_col="r_multiple_total"):
    return [TradeRef(r["actif"], r["direction"], r["ouvert_at"], r[r_col]) for r in rows]


def load_shadow_for_report(conn, source):
    epoch = conn.execute("SELECT started_at FROM shadow_epochs WHERE source = ?", (source,)).fetchone()
    if epoch is None:
        return None, [], []
    started_at = epoch["started_at"]
    closed = conn.execute(
        "SELECT actif, direction, ouvert_at, ferme_at, r_multiple_total FROM shadow_trades "
        "WHERE source = ? AND statut = 'ferme' AND ouvert_at >= ?", (source, started_at),
    ).fetchall()
    return started_at, _trade_refs(closed), closed


def load_live_baseline(conn, source, since):
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, r_multiple_total FROM trades "
        "WHERE source = ? AND statut = 'ferme' AND anomalie_technique IS NULL "
        "AND ouvert_at >= ? AND r_multiple_total IS NOT NULL", (source, since),
    ).fetchall()
    return _trade_refs(rows)


def main() -> int:
    from src.audit_notifier import send_notification
    from src.config import load_config

    config = load_config()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    try:
        reports = []
        pooled_for_cluster = []
        for source in SHADOW_CANDIDATES:
            started_at, shadow_refs, raw_rows = load_shadow_for_report(conn, source)
            baseline_refs = load_live_baseline(conn, BASELINE_SOURCE[source], started_at) if started_at else []
            report = build_hypothesis_report(source, started_at, shadow_refs, baseline_refs, EXPECTED_EFFECT[source], now)
            reports.append(report)
            for row in raw_rows:
                pooled_for_cluster.append(PortfolioTrade(source, row["actif"], row["ouvert_at"],
                                                          row["ferme_at"] or row["ouvert_at"], 10.0, row["r_multiple_total"]))

        cluster_rate = cluster_rejection_rate(pooled_for_cluster)
        date_iso = now.date().isoformat()
        markdown = format_weekly_markdown(reports, date_iso, cluster_rate)
        os.makedirs(REPORT_DIR, exist_ok=True)
        report_path = os.path.join(REPORT_DIR, f"{date_iso}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"Rapport écrit : {report_path}")

        telegram_message = format_weekly_telegram(reports)
        if send_notification(config.telegram_bot_token, config.telegram_chat_id, telegram_message):
            print("Résumé Telegram envoyé.")
        else:
            print("Envoi Telegram échoué — le rapport reste écrit sur disque, nouvel essai la semaine prochaine.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
