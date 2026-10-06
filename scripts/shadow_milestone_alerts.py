"""
shadow_milestone_alerts.py — Jalons/alertes/verdict forward des 4
candidates V2 en shadow (06/10/2026, voir
docs/PROTOCOLE_EVOLUTION_V2_06-10.md §6-7).

**NE PAS lancer avant l'activation explicite** (voir
docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md). Lecture seule, AUCUN appel broker
(contrairement à run_shadow_cycle.py) : lit `shadow_trades`/`trades`,
envoie au plus une alerte Telegram par jalon nouvellement atteint,
mémorise l'état dans `system_state` (même convention que
`scripts/milestone_alerts.py`, clé distincte). AUCUNE action automatique :
la promotion reste une décision d'Ismaël.

Cron prévu (à installer SEULEMENT après l'activation du shadow) :
    */15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/shadow_milestone_alerts.py >> logs/shadow_milestone_alerts_cron.log 2>&1
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.evolution_v2_test import TradeRef, decide_forward_verdict  # noqa: E402
from src.shadow_milestone import compute_status, format_milestone_message, format_verdict_message  # noqa: E402
from src.shadow_tracking import SHADOW_CANDIDATES  # noqa: E402

# brut_min repris de docs/EVOLUTIONS_CANDIDATES.md (05/10), jamais recalculé.
EXPECTED_EFFECT = {"hypothesis_v3cand": 0.24, "hypothesis2_v3cand": 0.13, "hypothesis3_v3cand": 0.13,
                   "hypothesis4_v3cand": 0.10}
BASELINE_SOURCE = {"hypothesis_v3cand": "hypothesis_v2", "hypothesis2_v3cand": "hypothesis2_v2",
                   "hypothesis3_v3cand": "hypothesis3_v2", "hypothesis4_v3cand": "hypothesis4_v2"}
ALERT_STATE_KEY = "shadow_milestone_alert:{source}:{milestone}"
VERDICT_STATE_KEY = "shadow_forward_verdict:{source}"


def load_shadow_closed(conn, source: str):
    epoch = conn.execute("SELECT started_at FROM shadow_epochs WHERE source = ?", (source,)).fetchone()
    if epoch is None:
        return None, []
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, r_multiple_total FROM shadow_trades "
        "WHERE source = ? AND statut = 'ferme' AND ouvert_at >= ?", (source, epoch["started_at"]),
    ).fetchall()
    trades = [TradeRef(r["actif"], r["direction"], r["ouvert_at"], r["r_multiple_total"]) for r in rows]
    return epoch["started_at"], trades


def load_live_baseline(conn, source: str, since: str):
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, r_multiple_total FROM trades "
        "WHERE source = ? AND statut = 'ferme' AND anomalie_technique IS NULL "
        "AND ouvert_at >= ? AND r_multiple_total IS NOT NULL", (source, since),
    ).fetchall()
    return [TradeRef(r["actif"], r["direction"], r["ouvert_at"], r["r_multiple_total"]) for r in rows]


def already_alerted(conn, source: str) -> list:
    rows = conn.execute(
        "SELECT value FROM system_state WHERE key = ?",
        (ALERT_STATE_KEY.format(source=source, milestone="all"),),
    ).fetchone()
    return json.loads(rows["value"]) if rows else []


def mark_alerted(conn, source: str, milestones: list, now_iso: str) -> None:
    conn.execute(
        "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (ALERT_STATE_KEY.format(source=source, milestone="all"), json.dumps(sorted(milestones)), now_iso),
    )


def main() -> int:
    from src.audit_notifier import send_notification
    from src.config import load_config

    config = load_config()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    try:
        for source in SHADOW_CANDIDATES:
            started_at, shadow_trades = load_shadow_closed(conn, source)
            if started_at is None:
                print(f"{source} : aucune époque shadow encore écrite — rien à faire")
                continue
            previously = already_alerted(conn, source)
            mean_r = sum(t.r_multiple for t in shadow_trades) / len(shadow_trades) if shadow_trades else None
            status = compute_status(source, len(shadow_trades), mean_r, started_at, now, previously)
            print(f"{source} : n={status.n_closed} semaines={status.weeks_elapsed:.1f} eligible={status.eligible} "
                  f"nouveaux={status.new_alerts}")
            if not status.new_alerts:
                continue

            baseline = load_live_baseline(conn, BASELINE_SOURCE[source], started_at)
            verdict = decide_forward_verdict(source, baseline, shadow_trades, EXPECTED_EFFECT[source])
            succeeded = []
            for milestone in status.new_alerts:
                message = format_milestone_message(status, milestone, verdict.mde)
                if send_notification(config.telegram_bot_token, config.telegram_chat_id, message):
                    print(message)
                    succeeded.append(milestone)
                else:
                    print(f"Envoi Telegram échoué pour {source} jalon {milestone} — nouvel essai au prochain passage")
            if not succeeded:
                continue
            mark_alerted(conn, source, sorted(set(previously) | set(succeeded)), now_iso)
            conn.commit()

            verdict_message = format_verdict_message(status, verdict)
            if send_notification(config.telegram_bot_token, config.telegram_chat_id, verdict_message):
                print(verdict_message)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
