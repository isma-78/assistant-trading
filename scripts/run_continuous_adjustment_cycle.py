"""
run_continuous_adjustment_cycle.py — Point 8 (29/08/2026, voir
docs/DECISIONS.md) : lanceur cron du cycle d'ajustement continu pour les
5 hypothèses, via `src.evolution_cycle_controller.run_all_enabled_cycles`
(interrupteur DB par hypothèse, plafond d'1 validation/30j, correction z
sur le compteur cumulé, budget de temps mur, fail-safe par hypothèse).

**Registre `HYPOTHESES` volontairement VIDE à ce stade** — pas un
oubli : ce cycle re-tune UNIQUEMENT des variables déjà pré-enregistrées
avec une grille validée par un premier calage statistique (n>=200,
raccourci point 14, gate de puissance) — c'est EXACTEMENT le travail du
point 17 (calibration H2-H5), pas encore exécuté au moment où ce
fichier est écrit. Câbler un `train_fn`/`validation_fn` par hypothèse
ICI avant que le point 17 ait produit son premier verdict reviendrait à
inventer une grille sans l'avoir validée sur l'entraînement — exactement
ce que l'invariant #10 interdit. Une fois le point 17 terminé, ajouter
une entrée par hypothèse qualifiée selon le même schéma que
`scripts/run_evolution_cycle.py::HYPOTHESES` (candidats + théorie déjà
écrite dans docs/HYPOTHESES.md).

Tant que `HYPOTHESES` est vide, ce script est un NO-OP documenté (aucun
risque à l'installer en cron dès maintenant) — voir
docs/DEPLOIEMENT_V2.md, étape 8.

`nice -n 19` est la responsabilité du CRON (jamais de ce script) — voir
la ligne crontab dans docs/DEPLOIEMENT_V2.md. `--max-wall-seconds`
(défaut 1800 = 30 min) protège les 6 process de trading qui partagent
les 2 cœurs du VPS : un cycle qui dépasse ce budget s'interrompt
proprement (résultat partiel rapporté), jamais un SIGKILL brutal.

Usage :
    python scripts/run_continuous_adjustment_cycle.py
    python scripts/run_continuous_adjustment_cycle.py --max-wall-seconds 900
"""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit_notifier import send_notification
from src.config import load_config
from src.evolution_cycle_controller import run_all_enabled_cycles

logger = logging.getLogger(__name__)

# Voir docstring ci-dessus : rempli hypothèse par hypothèse une fois le
# point 17 (calibration H2-H5) terminé et le chantier H1 (point 15,
# closhttp côté recherche) rouvert par une nouvelle piste théorique.
HYPOTHESES: dict = {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-wall-seconds", type=float, default=1800.0)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    config = load_config()
    db_path = args.db or config.db_path

    if not HYPOTHESES:
        logger.info(
            "Registre HYPOTHESES vide (voir docstring) — aucun cycle a tourner, "
            "en attente du premier calage valide (point 17)."
        )
        return

    now_iso = datetime.now(timezone.utc).isoformat()

    def notify_fn(row: dict) -> None:
        try:
            message = (
                f"Nouvelle proposition d'evolution — {row['variable']}\n"
                f"Constat : {row['constat_stat']}\n"
                f"Ajustement propose : {row['ajustement_propose']}\n"
                f"Statut : propose (jamais applique automatiquement)."
            )
            send_notification(config.telegram_bot_token, config.telegram_chat_id, message)
        except Exception:
            logger.exception("Notification Telegram echouee pour %s (proposition deja ecrite en base).", row["variable"])

    results = run_all_enabled_cycles(
        HYPOTHESES, db_path, now_iso, max_wall_seconds=args.max_wall_seconds, notify_fn=notify_fn,
    )
    for name, outcome in results.items():
        logger.info("%s : ran=%s reason=%s", name, outcome.ran, outcome.reason)


if __name__ == "__main__":
    main()
