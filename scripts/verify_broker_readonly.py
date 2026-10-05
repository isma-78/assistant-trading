"""
verify_broker_readonly.py — A1 du bilan du 05/10/2026 : état RÉEL côté
broker des positions des comptes démo H2/H3/H4 (lecture seule), comparé à
la base. À lancer par Ismaël lui-même (docs/APPLICATION_05-10.md).

Ce que le script fait, et rien d'autre :
- vérifie PAR CODE que l'environnement est DÉMO (config + URL codée en
  dur) — sinon il s'arrête avant tout appel réseau ;
- par compte : ouverture d'une session (POST /session), choix du compte
  (PUT /session), puis UN SEUL appel GET /positions. Aucun ordre, aucune
  clôture, aucune modification de stop ;
- appels séquentiels espacés d'au moins 2 s, budget de 10 appels HTTP au
  total (3 comptes × 3 appels = 9) ; au premier 429, arrêt immédiat ;
- compare chaque position à la base (lecture seule, `mode=ro`) :
  présente dans `trades.deal_id` / `trade_legs.position_deal_id` ou non,
  stop présent ou non, âge, hypothèse d'origine ; signale les 6 ordres de
  palier « NON annulés » et les 4 positions du combo H2 retiré.

Pourquoi il n'a PAS été lancé par l'agent : le mandat du 05/10 interdisait
de créer une session. Lire les positions d'un compte exige une session et
un choix de compte (`PUT /session`), et le compte « préféré » d'un
identifiant Capital.com est un état partagé entre clés API (incident du
20/08/2026, docstring de `CapitalClient.switch_account`). Les exécuteurs
ciblent toujours leur compte explicitement et se réauthentifient seuls
(correctif du 01/09/2026) : le risque est faible, mais la décision
appartient à Ismaël.

Usage (sur le VPS, depuis /home/assistant/assistant-trading) :
    venv/bin/python scripts/verify_broker_readonly.py
"""

import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
MIN_SPACING_SECONDS = 2.0
MAX_HTTP_CALLS = 10

# Relevés du bilan du 05/10/2026 (docs/BILAN_05-10.md, §1.4 et §4 A1).
UNCANCELLED_LEG_ORDERS = {
    "00000000-6698-aeca-048d-1d960015549e": "hypothesis3_v2",
    "00000000-65ea-4b0b-048d-62f90015549e": "hypothesis4_v2",
    "00000000-65ea-4b29-048d-62f90015549e": "hypothesis4_v2",
    "00000000-66b1-8638-048d-62f90015549e": "hypothesis4_v2",
    "00000000-66b1-8d06-048d-62f90015549e": "hypothesis4_v2",
    "00000000-66b1-8d2e-048d-62f90015549e": "hypothesis4_v2",
}
RETIRED_COMBO_TRADE_IDS = (14877, 15948, 15954, 16329)


class RateLimited(RuntimeError):
    pass


def assert_demo(config) -> None:
    """Garde-fou invariant #4 : jamais un appel si l'environnement n'est
    pas DÉMO, quelle que soit la raison."""
    if config.capital_environment != "demo":
        raise SystemExit(f"ARRÊT : CAPITAL_ENVIRONMENT={config.capital_environment!r}, pas 'demo'. Aucun appel effectué.")
    if "demo-api" not in DEMO_BASE_URL:
        raise SystemExit("ARRÊT : URL broker non démo. Aucun appel effectué.")


def classify_positions(positions, db_index, now=None):
    """Logique pure (testée) : une ligne de rapport par position broker.

    `positions` : liste brute de GET /positions. `db_index` : dict
    {deal_id: (trade_id, source, actif, statut)} construit depuis la base.
    """
    now = now or datetime.now(timezone.utc)
    report = []
    for item in positions:
        pos = item.get("position", {})
        market = item.get("market", {})
        deal_id = pos.get("dealId")
        working_order_id = pos.get("workingOrderId")
        created = pos.get("createdDateUTC")
        age_hours = None
        if created:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_hours = round((now - created_at).total_seconds() / 3600, 1)
        db_hit = db_index.get(deal_id) or db_index.get(working_order_id)
        stop_level = pos.get("stopLevel")
        level = pos.get("level")
        size = pos.get("size")
        risk_quote = abs(level - stop_level) * size if (level is not None and stop_level is not None and size) else None
        report.append({
            "deal_id": deal_id,
            "working_order_id": working_order_id,
            "epic": market.get("epic"),
            "direction": pos.get("direction"),
            "size": size,
            "level": level,
            "stop_level": stop_level,
            "stop_present": stop_level is not None,
            "guaranteed": bool(pos.get("guaranteedStop")),
            "age_hours": age_hours,
            "in_db": db_hit is not None,
            "db": db_hit,
            "uncancelled_leg_order": UNCANCELLED_LEG_ORDERS.get(working_order_id),
            "risk_quote_currency": risk_quote,
            "critical": db_hit is None or stop_level is None,
        })
    return report


def build_db_index(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    index = {}
    for trade_id, source, actif, statut, deal_id in conn.execute(
        "SELECT id, source, actif, statut, deal_id FROM trades WHERE deal_id IS NOT NULL"
    ):
        index[deal_id] = (trade_id, source, actif, statut)
    for trade_id, source, actif, statut, order_id, position_id in conn.execute(
        "SELECT t.id, t.source, t.actif, t.statut, l.order_deal_id, l.position_deal_id "
        "FROM trade_legs l JOIN trades t ON t.id = l.trade_id"
    ):
        for key in (order_id, position_id):
            if key:
                index[key] = (trade_id, source, actif, statut)
    conn.close()
    return index


def estimate_eur_risk(db_path: str, row: dict):
    """Risque en € d'une position hors base : celui de l'ordre annulé du
    même actif et même niveau d'entrée (le palier appartenait à ce trade),
    au prorata de la taille. None si aucun rapprochement possible."""
    if row["level"] is None or not row["size"]:
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    hit = conn.execute(
        "SELECT risque_eur, taille_initiale FROM trades WHERE actif = ? AND statut = 'annule' "
        "AND ABS(prix_entree_prevu - ?) <= 1e-9 * MAX(1, ABS(?)) ORDER BY id DESC LIMIT 1",
        (row["epic"], row["level"], row["level"]),
    ).fetchone()
    conn.close()
    if hit is None or not hit[1]:
        return None
    return round(hit[0] * row["size"] / hit[1], 2)


def main() -> int:
    config = load_config()
    assert_demo(config)
    accounts = [
        ("H2", config.capital_api_key_hypothesis2, config.capital_identifier_hypothesis2,
         config.capital_api_password_hypothesis2, config.capital_account_id_hypothesis2),
        ("H3", config.capital_api_key_hypothesis3, config.capital_identifier_hypothesis3,
         config.capital_api_password_hypothesis3, config.capital_account_id_hypothesis3),
        ("H4", config.capital_api_key_hypothesis4, config.capital_identifier_hypothesis4,
         config.capital_api_password_hypothesis4, config.capital_account_id_hypothesis4),
    ]
    db_index = build_db_index(config.db_path)
    calls = 0
    last_call = 0.0

    def spaced(fn, *args):
        nonlocal calls, last_call
        if calls >= MAX_HTTP_CALLS:
            raise RuntimeError("Budget de 10 appels atteint — arrêt.")
        wait = MIN_SPACING_SECONDS - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        calls += 1
        last_call = time.monotonic()
        try:
            return fn(*args)
        except CapitalApiError as exc:
            if "429" in str(exc) or "too-many" in str(exc):
                raise RateLimited(str(exc)) from exc
            raise

    any_critical = False
    try:
        for label, key, ident, pwd, account_id in accounts:
            if not (key and ident and pwd and account_id):
                print(f"[{label}] identifiants incomplets dans .env — compte ignoré")
                continue
            client = CapitalClient(key, ident, pwd, DEMO_BASE_URL)
            spaced(client.login)
            spaced(client.switch_account, account_id)
            positions = spaced(client.get_open_positions)
            print(f"\n[{label}] {len(positions)} position(s) ouverte(s) côté broker")
            for row in classify_positions(positions, db_index):
                eur = None if row["in_db"] else estimate_eur_risk(config.db_path, row)
                flag = "CRITIQUE " if row["critical"] else ""
                print(
                    f"  {flag}{row['epic']} {row['direction']} taille={row['size']} entrée={row['level']} "
                    f"stop={'oui ' + str(row['stop_level']) if row['stop_present'] else 'NON'} "
                    f"garanti={row['guaranteed']} âge={row['age_hours']}h "
                    f"base={'oui ' + str(row['db']) if row['in_db'] else 'NON (hors base)'} "
                    f"ordre_palier_non_annulé={row['uncancelled_leg_order'] or '-'} "
                    f"risque≈{row['risk_quote_currency']} (devise de cotation)"
                    + (f", ≈{eur} €" if eur is not None else "")
                )
                any_critical = any_critical or row["critical"]
            if label == "H2":
                open_trade_ids = {r["db"][0] for r in classify_positions(positions, db_index) if r["db"]}
                for trade_id in RETIRED_COMBO_TRADE_IDS:
                    state = "OUVERTE chez le broker" if trade_id in open_trade_ids else "ABSENTE des positions broker"
                    print(f"  combo H2 retiré, trade {trade_id} : {state}")
    except RateLimited as exc:
        print(f"\n429 reçu ({exc}) — ARRÊT immédiat des appels, relancer plus tard.")
        return 2
    print(f"\nAppels HTTP effectués : {calls}/{MAX_HTTP_CALLS}. Constat critique : {'OUI' if any_critical else 'non'}")
    return 1 if any_critical else 0


if __name__ == "__main__":
    sys.exit(main())
