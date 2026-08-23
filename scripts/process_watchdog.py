"""
process_watchdog.py — Surveille les process critiques du VPS
(telegram_listener, executor_loop, trend_executor, control_bot,
hypothesis3_executor, hypothesis2_executor, hypothesis4_executor et
depuis le 23/08/2026 hypothesis5_executor — voir docs/DECISIONS.md) et
alerte via le bot Telegram (même canal que les coupe-circuits) si l'un
d'eux a disparu.
Ne relance JAMAIS un
process automatiquement (demande explicite d'Ismaël, 19/08/2026) : un
redémarrage aveugle masquerait la cause de l'arrêt et pourrait
reproduire un état incohérent en silence — la décision de relancer
reste manuelle, ce script se contente d'alerter et de journaliser.

Invoqué par CRON (voir crontab du VPS, `*/5 * * * *`), PAS par un
process tmux dédié à lui : un watchdog qui tournerait lui-même comme
process long-lived aurait exactement le même point de défaillance que ce
qu'il surveille (« qui surveille le surveillant ? »). cron est géré par
le système d'init du VPS, indépendant de tout process applicatif — voir
docs/DECISIONS.md pour l'investigation de la mort silencieuse de
trend_executor le 19/08/2026 qui a motivé ce choix.

Détection par `pgrep -f "python -m <module>"` sur la ligne de commande
complète plutôt que par présence d'une session tmux : observé en
production que les deux se désynchronisent (une session tmux créée sur
un shell interactif peut survivre au crash du process qu'elle contenait ;
une session tmux "one-shot" peut au contraire disparaître entièrement
sans laisser de trace) — voir docs/DECISIONS.md.

État persisté dans `system_state` (même table que circuit_breaker_store,
survit à un redémarrage du VPS) : une alerte est envoyée une seule fois
par transition vivant->mort (pas de spam à chaque exécution cron tant
que le process reste absent), et une notification de reprise est
envoyée au retour vivant.

Usage :
    python scripts/process_watchdog.py

Cron (crontab du VPS) :
    */5 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/process_watchdog.py >> logs/watchdog_cron.log 2>&1
"""

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# scripts/ n'est pas un package installé : insère la racine du dépôt sur
# sys.path pour que `import src...` fonctionne quel que soit le
# répertoire courant d'où ce script est lancé (cron l'invoque après un
# `cd` explicite, mais un lancement manuel direct — `python
# scripts/process_watchdog.py` — ne place PAS automatiquement la racine
# du dépôt sur sys.path, seulement le dossier scripts/ lui-même).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit_notifier import send_notification  # noqa: E402
from src.db import connection_scope  # noqa: E402

logger = logging.getLogger(__name__)

# Nom lisible -> module lancé via `python -m <module>` (voir les
# commandes tmux réellement utilisées au déploiement, docs P2/P2.5/P2.6
# de CLAUDE.md).
PROCESSES = {
    "telegram_listener": "src.telegram_listener",
    "executor_loop": "src.executor",
    "trend_executor": "src.trend_executor",
    "control_bot": "src.control_bot",
    "hypothesis3_executor": "src.hypothesis3_executor",
    "hypothesis2_executor": "src.hypothesis2_executor",
    "hypothesis4_executor": "src.hypothesis4_executor",
    "hypothesis5_executor": "src.hypothesis5_executor",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_process_alive(module_path: str) -> bool:
    """Vrai si au moins un process `python -m <module_path>` tourne
    actuellement (pgrep -f, ligne de commande complète)."""
    result = subprocess.run(["pgrep", "-f", f"python -m {module_path}"], capture_output=True, text=True)
    return result.returncode == 0


def _get_state(db_path: str, key: str) -> Optional[str]:
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def _set_state(db_path: str, key: str, value: str) -> None:
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, _now_iso()),
        )


def check_process(
    db_path: str, name: str, module_path: str,
    bot_token: Optional[str], chat_id: Optional[str],
) -> str:
    """Vérifie un process, journalise systématiquement, alerte
    uniquement à la transition vivant->mort (et notifie la reprise
    mort->vivant). Retourne "up" ou "down"."""
    key = f"watchdog:status:{name}"
    previous = _get_state(db_path, key)  # None | "up" | "down:<iso>"
    alive = is_process_alive(module_path)

    if alive:
        logger.info("Process %s : vivant", name)
        if previous is not None and previous.startswith("down:"):
            down_since = previous.split(":", 1)[1]
            logger.warning("Process %s de nouveau actif (était arrêté depuis %s)", name, down_since)
            if bot_token and chat_id:
                send_notification(
                    bot_token, chat_id,
                    f"✅ {name} de nouveau actif (était arrêté depuis {down_since})",
                )
        _set_state(db_path, key, "up")
        return "up"

    logger.warning("Process %s : ABSENT", name)
    if previous is None or previous == "up":
        down_since = _now_iso()
        _set_state(db_path, key, f"down:{down_since}")
        logger.error("ALERTE : process %s manquant (première détection à %s)", name, down_since)
        if bot_token and chat_id:
            send_notification(
                bot_token, chat_id,
                f"\U0001F6A8 Process manquant : {name}\n"
                f"Détecté arrêté depuis {down_since}\n"
                "Aucun redémarrage automatique — intervention manuelle requise.",
            )
    else:
        down_since = previous.split(":", 1)[1]
        logger.warning("Process %s toujours absent (depuis %s, déjà notifié)", name, down_since)
    return "down"


def run_watchdog_check(config, db_path: str) -> dict:
    results = {}
    for name, module_path in PROCESSES.items():
        results[name] = check_process(db_path, name, module_path, config.telegram_bot_token, config.telegram_chat_id)
    logger.info("Vérification watchdog terminée : %s", results)
    return results


def main() -> None:
    from src.config import load_config
    from src.db import init_db

    config = load_config()
    init_db(config.db_path)
    run_watchdog_check(config, config.db_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
