"""
_verif_etat_broker_08-10.py -- Etape 1(c) du mandat de reprise du
08/10/2026 : lecture seule, compare les positions REELLEMENT ouvertes au
broker (5 comptes demo : main/StationX+H1, H2, H3, H4, H5) aux trades
`statut='ouvert'` en base, pour reperer les ecarts dans les deux sens
(fantome cote base = aucune position broker correspondante ; position
broker non suivie = aucun trade base correspondant).

Verifie PAR CODE l'environnement demo avant tout appel. Un seul GET
/positions par compte (login + switch_account + positions = 3 appels/
compte). Budget : 5 comptes x 3 appels max = 15 appels HTTP, espacement
>=3s, arret immediat au premier 429. Aucune ecriture, aucun ordre,
aucune fermeture.
"""

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
MIN_SPACING_SECONDS = 3.0
MAX_HTTP_CALLS = 16


def log(msg: str) -> None:
    print(msg, flush=True)


class Budget:
    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.used = 0
        self._last_call = 0.0

    def spaced(self, fn, *a, **kw):
        if self.used >= self.max_calls:
            raise RuntimeError("Budget d'appels HTTP epuise.")
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < MIN_SPACING_SECONDS:
            time.sleep(MIN_SPACING_SECONDS - elapsed)
        result = fn(*a, **kw)
        self.used += 1
        self._last_call = time.monotonic()
        return result


def main() -> int:
    config = load_config()
    if config.capital_environment != "demo":
        log(f"ARRET : CAPITAL_ENVIRONMENT={config.capital_environment!r}, attendu 'demo'. Aucun appel effectue.")
        return 1

    accounts = [
        ("main/StationX+H1", config.capital_api_key, config.capital_identifier,
         config.capital_api_password, config.capital_account_id),
        ("H2", config.capital_api_key_hypothesis2, config.capital_identifier_hypothesis2,
         config.capital_api_password_hypothesis2, config.capital_account_id_hypothesis2),
        ("H3", config.capital_api_key_hypothesis3, config.capital_identifier_hypothesis3,
         config.capital_api_password_hypothesis3, config.capital_account_id_hypothesis3),
        ("H4", config.capital_api_key_hypothesis4, config.capital_identifier_hypothesis4,
         config.capital_api_password_hypothesis4, config.capital_account_id_hypothesis4),
        ("H5", config.capital_api_key_hypothesis5, config.capital_identifier_hypothesis5,
         config.capital_api_password_hypothesis5, config.capital_account_id_hypothesis5),
    ]

    budget = Budget(MAX_HTTP_CALLS)
    broker_positions = {}  # deal_id -> (label, epic, direction, size)

    for label, api_key, identifier, password, account_id in accounts:
        if not (api_key and identifier and password):
            log(f"[{label}] identifiants incomplets -- compte saute.")
            continue
        client = CapitalClient(api_key, identifier, password, DEMO_BASE_URL)
        try:
            budget.spaced(client.login)
            if account_id:
                budget.spaced(client.switch_account, account_id)
            positions = budget.spaced(client.get_open_positions)
        except CapitalApiError as exc:
            if "429" in str(exc):
                log(f"429 recu ({exc}) -- ARRET IMMEDIAT, aucune action tentee.")
                break
            log(f"[{label}] erreur API ({exc}) -- compte saute.")
            continue
        log(f"[{label}] {len(positions)} position(s) ouverte(s) au broker.")
        for pos in positions:
            market = pos.get("market", {})
            deal = pos.get("position", {})
            deal_id = deal.get("dealId")
            broker_positions[deal_id] = (label, market.get("epic"), deal.get("direction"), deal.get("size"))

    log(f"\nAppels HTTP consommes : {budget.used}/{MAX_HTTP_CALLS}")

    db_path = config.db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    db_rows = conn.execute(
        "SELECT id, actif, source, direction, risque_eur, deal_id FROM trades WHERE statut='ouvert'"
    ).fetchall()
    # Un trade `statut='ouvert'` est represente au broker par 1 a 3 positions
    # distinctes (tp1/tp2/runner, `trade_legs.position_deal_id`), jamais par
    # le seul `trades.deal_id` (qui ne pointe que sur la jambe tp1 d'origine,
    # perimee dès qu'elle cloture) -- voir docs/DECISIONS.md 08/10 (reprise 2).
    leg_rows = conn.execute(
        "SELECT trade_id, palier, position_deal_id FROM trade_legs WHERE statut='ouvert'"
    ).fetchall()
    tracked_deal_ids = {r["deal_id"] for r in db_rows if r["deal_id"]}
    tracked_deal_ids |= {r["position_deal_id"] for r in leg_rows if r["position_deal_id"]}
    legs_by_deal = {r["position_deal_id"]: r for r in leg_rows if r["position_deal_id"]}

    log(f"\nBase ({db_path}) : {len(db_rows)} trade(s) statut='ouvert', {len(leg_rows)} jambe(s) statut='ouvert'.")
    log(f"Broker (comptes interroges) : {len(broker_positions)} position(s) ouverte(s).")

    log("\n--- Positions broker SANS trade/jambe base correspondant (non suivies) ---")
    untracked = [deal_id for deal_id in broker_positions if deal_id not in tracked_deal_ids]
    for deal_id in untracked:
        label, epic, direction, size = broker_positions[deal_id]
        log(f"  [{label}] {epic} {direction} taille={size} dealId={deal_id}")
    if not untracked:
        log("  (aucune)")

    log("\n--- Trades base statut='ouvert' dont AUCUNE jambe n'a de position broker correspondante (fantomes) ---")
    trade_leg_deal_ids = {}
    for r in leg_rows:
        trade_leg_deal_ids.setdefault(r["trade_id"], set()).add(r["position_deal_id"])
    ghosts = []
    for r in db_rows:
        leg_deals = trade_leg_deal_ids.get(r["id"], set())
        all_deal_ids = leg_deals | ({r["deal_id"]} if r["deal_id"] else set())
        if all_deal_ids and not (all_deal_ids & set(broker_positions.keys())):
            ghosts.append(r)
    for r in ghosts:
        log(f"  id={r['id']} {r['actif']} {r['source']} {r['direction']} risque_eur={r['risque_eur']} deal_id={r['deal_id']}")
    if not ghosts:
        log("  (aucun)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
