"""
capture_financing.py — Point 7 (29/08/2026, voir docs/DECISIONS.md) :
capture quotidienne des transactions SWAP (financement réel débité par
Capital.com) sur le compte principal. Fenêtre broker plafonnée à 24h
(`financing_capture.MAX_LAST_PERIOD_SECONDS`) — CE SCRIPT DOIT TOURNER
AU MOINS UNE FOIS PAR JOUR (cron), sinon les transactions de la fenêtre
manquée sont perdues DÉFINITIVEMENT (aucun rattrapage possible auprès
du broker au-delà de 24h).

Utilise les identifiants du compte PRINCIPAL (`config.capital_*`, même
compte qu'`executor.py`/`trend_executor.py`) — le financement débité
dépend de l'ACTIF et du SENS de la position, jamais du compte qui la
détient ; un seul compte suffit à observer le taux réel appliqué par
Capital.com sur chaque instrument, quel que soit le compte qui trade
dessus.

Lecture seule côté broker (`GET /history/transactions`), écriture
strictement limitée à `financing_transactions` (table dédiée, jamais
`trades`) — aucun ordre, aucune interaction avec les 6 process de
trading en cours. Usage :
    python scripts/capture_financing.py
"""

import logging
import os
import sys
from datetime import datetime, timezone

# A4 (bilan du 05/10/2026) : lancé par cron depuis la racine du dépôt avec
# `venv/bin/python scripts/capture_financing.py`, Python ne met que
# `scripts/` dans sys.path — `import src...` échouait chaque nuit
# (ModuleNotFoundError) depuis le 30/08/2026. Même correctif que
# scripts/epoch_status.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402
from src.db import init_db  # noqa: E402
from src.financing_capture import capture_recent_financing  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    if config.capital_environment != "demo":
        raise SystemExit("REFUS : CAPITAL_ENVIRONMENT n'est pas 'demo' — aucun appel broker.")
    init_db(config.db_path)

    client = CapitalClient(
        config.capital_api_key, config.capital_identifier, config.capital_api_password,
        "https://demo-api-capital.backend-capital.com/api/v1",
    )
    client.login()
    client.switch_account(config.capital_account_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    inserted = capture_recent_financing(client, config.db_path, now_iso)
    logger.info("Capture financement : %d nouvelle(s) transaction(s) SWAP persistée(s).", inserted)


if __name__ == "__main__":
    main()
