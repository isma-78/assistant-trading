"""
run_shadow_couples_cycle.py — Un cycle de suivi forward en SHADOW de
TOUS les couples (hypothèse _v2 actuellement déployée × actif de la
liste blanche), Partie 4 du 08/10/2026 (voir docs/COUPLES_V2_08-10.md
étape 5). Étiquette `_shadow_couples` (`src.shadow_tracking.SHADOW_
COUPLES`), distincte de `_v3cand`.

**NON branché au déploiement** : pas de cron installé par ce mandat,
aucune activation avant que `docs/CHECKLIST_10-10_UNIQUE.md` le décrive
explicitement ET que son prérequis (surveillance de 60 minutes +
shadow `_v3cand` déjà validé) soit rempli.

Lecture seule côté broker (prix/bougies, mêmes appels que les
exécuteurs réels et que `run_shadow_cycle.py`). AUCUN ordre, jamais —
même mécanique générique que les 4 candidates V2, aucune logique
nouvelle. Écrit uniquement dans
`shadow_trades`/`shadow_partials`/`shadow_epochs`.

Utilise le compte PRINCIPAL (mêmes identifiants qu'`executor.py`) : les
prix/bougies ne dépendent pas du compte qui les lit.
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
from src.shadow_tracking import SHADOW_COUPLES, run_shadow_cycle  # noqa: E402

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
                        whitelist={})  # le sizing réel est hors-sujet en shadow (seul le R compte)
    closed = run_shadow_cycle(config.db_path, client, engine, stop_refusal_model=StopRefusalModel(),
                              candidates=SHADOW_COUPLES)
    print(f"Cycle shadow_couples terminé : {closed} position(s) virtuelle(s) fermée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
