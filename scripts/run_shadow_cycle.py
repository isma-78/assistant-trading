"""
run_shadow_cycle.py — Un cycle de suivi forward en SHADOW des 4
candidates V2 (06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md).

**NE PAS lancer avant l'activation explicite décrite dans
`docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md`** (après le redéploiement
principal du 10/10, une fois ses propres vérifications validées).

Lecture seule côté broker (prix/snapshots, mêmes appels que les 6
exécuteurs réels). AUCUN ordre, jamais — `src/shadow_tracking.py` ne
référence aucune méthode d'écriture de `CapitalClient`. Écrit uniquement
dans `shadow_trades`/`shadow_partials`/`shadow_epochs`.

Utilise le compte PRINCIPAL (mêmes identifiants qu'`executor.py`) : les
prix/bougies ne dépendent pas du compte qui les lit, aucune raison
d'ouvrir une session dédiée.

Cron prévu (à installer SEULEMENT après l'activation, voir runbook) :
    */15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/run_shadow_cycle.py >> logs/shadow_cycle_cron.log 2>&1
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402
from src.db import init_db  # noqa: E402
from src.log_setup import configure_logging  # noqa: E402
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import StopRefusalModel  # noqa: E402
from src.shadow_tracking import run_shadow_cycle  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"


def main() -> int:
    configure_logging()
    config = load_config()
    if config.capital_environment != "demo":
        print("REFUS : CAPITAL_ENVIRONMENT n'est pas 'demo' — aucun appel broker.")
        return 3
    init_db(config.db_path)

    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id)

    engine = RiskEngine(caps=RiskCaps(config.risk_percent_default, config.risk_percent_boosted, config.envelope_initial),
                        whitelist={})  # le sizing réel est hors-sujet en shadow (seul le R compte, voir shadow_tracking.py)
    closed = run_shadow_cycle(config.db_path, client, engine, stop_refusal_model=StopRefusalModel())
    print(f"Cycle shadow terminé : {closed} position(s) virtuelle(s) fermée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
