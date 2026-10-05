"""
milestone_alerts.py — Décision 7 du 05/10/2026 : alerte Telegram quand une
hypothèse atteint n=30, 40 ou 53 trades réconciliés dans son époque, avec
l'espérance du moment. AUCUNE action automatique : le script lit, envoie un
message, mémorise le jalon déjà annoncé (`system_state`), rien d'autre. Le
verdict reste une décision humaine, selon le protocole pré-enregistré.

Comptage : src/verdict_counter.py (même définition qu'epoch_status.py).
Espérance : moyenne des R réels (prix d'exécution broker, spread inclus,
financement NON inclus tant que sa capture n'est pas réparée et rattrapée).

Cron prévu (installé au redéploiement, voir docs/RUNBOOK_REDEPLOIEMENT_10-10.md) :
    */15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/milestone_alerts.py >> logs/milestone_alerts_cron.log 2>&1
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.verdict_counter import count_all, reached_milestones  # noqa: E402

STATE_KEY = "milestone_alert:{source}:{epoch}"


def format_message(source: str, epoch: str, milestone: int, n: int, mean_r) -> str:
    mean = f"{mean_r:+.3f} R" if mean_r is not None else "n/a"
    return (
        f"📊 Jalon atteint — {source} (époque {epoch}) : {n} trades réconciliés (jalon {milestone}). "
        f"Espérance du moment : {mean} (spread inclus, financement non inclus). "
        "Aucune action automatique : le verdict se fait selon le protocole pré-enregistré."
    )


def pending_alerts(counts, last_alerted: dict):
    """Logique pure : [(clé d'état, nouveau n mémorisé, message)] pour chaque
    jalon franchi depuis le dernier relevé mémorisé."""
    alerts = []
    for count in counts:
        key = STATE_KEY.format(source=count.source, epoch=count.epoch)
        previous = last_alerted.get(key, 0)
        for milestone in reached_milestones(previous, count.n):
            alerts.append((key, milestone, format_message(count.source, count.epoch, milestone, count.n, count.mean_r)))
    return alerts


def main() -> int:
    from src.audit_notifier import send_notification
    from src.config import load_config

    config = load_config()
    counts = count_all(config.db_path)
    conn = sqlite3.connect(config.db_path)
    try:
        last = {k: int(v) for k, v in conn.execute("SELECT key, value FROM system_state WHERE key LIKE 'milestone_alert:%'")}
        for key, milestone, message in pending_alerts(counts, last):
            if send_notification(config.telegram_bot_token, config.telegram_chat_id, message):
                conn.execute(
                    "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, str(milestone), datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                print(message)
            else:
                print(f"Envoi Telegram échoué pour {key} (jalon {milestone}) — nouvel essai au prochain passage")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
