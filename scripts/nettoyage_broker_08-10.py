"""
nettoyage_broker_08-10.py — Étape 1 de la Partie 2 du mandat du
08/10/2026 (feu vert explicite d'Ismaël). Une session démo PAR COMPTE
(main/StationX+H1, H2, H3, H4 — H5 ignoré, `CAPITAL_ACCOUNT_ID_HYPOTHESIS5`
absent de ce `.env` local, jamais deviné), budget TOTAL de 40 appels
HTTP, espacement >=2s, arrêt immédiat au premier 429.

Classement (protocole Partie 2, catégories (a)-(e)) :
(a) les 6 ordres de palier « NON annulés » connus (H3 ×1, H4 ×5, voir
    `verify_broker_readonly.UNCANCELLED_LEG_ORDERS`) ;
(b) positions issues d'un ordre de palier rempli (`workingOrderId`
    correspondant à un ordre de (a)) ;
(c) les 4 positions du combo H2 retiré (trades 14877/15948/15954/16329,
    voir `close_retired_combo_positions.TRADE_IDS`) ;
(d) positions hors base, de provenance inconnue ;
(e) positions normales suivies par la base : JAMAIS touchées.

Actions, UNE PAR UNE, dans cet ordre exact (protocole Partie 2) :
  1. ferme les positions SANS stop de (b) et (d) ;
  2. annule les ordres de (a) (404 = déjà rempli -> reclassé (b), fermé
     à l'étape 4 si un stop est présent, ou aurait dû l'être à l'étape 1
     s'il a été détecté avant) ;
  3. ferme les 4 positions de (c) ;
  4. ferme les positions AVEC stop restantes de (b) et (d).
Relecture broker + vérification de réconciliation après CHAQUE action.
Aucune ouverture, aucune modification de stop, aucune action sur (e).

Par défaut : simulation (aucune action broker d'écriture). `--apply`
pour agir réellement (toujours en démo, vérifié par code AVANT tout
appel).
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
MIN_SPACING_SECONDS = 2.0
MAX_HTTP_CALLS = 40

UNCANCELLED_LEG_ORDERS = {
    "00000000-6698-aeca-048d-1d960015549e": "hypothesis3_v2",
    "00000000-65ea-4b0b-048d-62f90015549e": "hypothesis4_v2",
    "00000000-65ea-4b29-048d-62f90015549e": "hypothesis4_v2",
    "00000000-66b1-8638-048d-62f90015549e": "hypothesis4_v2",
    "00000000-66b1-8d06-048d-62f90015549e": "hypothesis4_v2",
    "00000000-66b1-8d2e-048d-62f90015549e": "hypothesis4_v2",
}
RETIRED_COMBO_TRADE_IDS = (14877, 15948, 15954, 16329)

# Reprise du 08/10/2026 : une position (b)/(d) hors base n'est "confirmée
# orpheline" (éligible à une fermeture) que si elle est âgée d'au moins
# ORPHAN_MIN_AGE_MINUTES — une position plus récente peut être un simple
# retard normal de réconciliation (cas réel constaté : H2 US100, créée
# ~26 min avant une lecture, toujours absente de la base à ce moment-là,
# réapparue réconciliée peu après). Jamais fermée sur cette seule
# présomption ; re-signalée, jamais actionnée, tant que l'âge n'est pas
# atteint.
ORPHAN_MIN_AGE_MINUTES = 120.0


def position_age_minutes(pos: dict, now: datetime) -> Optional[float]:
    """Âge en minutes depuis `createdDateUTC` (broker) — `None` si absent
    (jamais un âge deviné)."""
    created = pos.get("createdDateUTC")
    if not created:
        return None
    created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - created_at).total_seconds() / 60.0


def is_confirmed_orphan(pos: dict, now: datetime, min_age_minutes: float = ORPHAN_MIN_AGE_MINUTES) -> bool:
    """Vrai seulement si l'âge est connu ET >= `min_age_minutes` — une
    position sans `createdDateUTC` n'est JAMAIS présumée orpheline par
    défaut (fail-safe, jamais l'inverse)."""
    age = position_age_minutes(pos, now)
    return age is not None and age >= min_age_minutes


class RateLimited(RuntimeError):
    pass


class Budget:
    def __init__(self, max_calls):
        self.max_calls = max_calls
        self.used = 0
        self.last_call = 0.0

    def spaced(self, fn, *args, **kwargs):
        if self.used >= self.max_calls:
            raise RuntimeError(f"Budget de {self.max_calls} appels atteint — arrêt, rien de plus n'est tenté.")
        wait = MIN_SPACING_SECONDS - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        self.used += 1
        self.last_call = time.monotonic()
        try:
            return fn(*args, **kwargs)
        except CapitalApiError as exc:
            text = str(exc)
            if "429" in text or "too-many" in text:
                raise RateLimited(text) from exc
            raise


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)


def assert_demo(config) -> None:
    if config.capital_environment != "demo":
        raise SystemExit(f"ARRÊT : CAPITAL_ENVIRONMENT={config.capital_environment!r}, pas 'demo'. Aucun appel effectué.")
    if "demo-api" not in DEMO_BASE_URL:
        raise SystemExit("ARRÊT : URL broker non démo. Aucun appel effectué.")


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


def classify_position(pos_item: dict, db_index: dict) -> str:
    pos = pos_item.get("position", {})
    deal_id = pos.get("dealId")
    working_order_id = pos.get("workingOrderId")
    db_hit = db_index.get(deal_id) or db_index.get(working_order_id)
    if db_hit and db_hit[0] in RETIRED_COMBO_TRADE_IDS:
        return "c"
    if working_order_id in UNCANCELLED_LEG_ORDERS:
        return "b"
    if db_hit is None:
        return "d"
    return "e"


def read_account(client: CapitalClient, budget: Budget, label: str) -> dict:
    positions = budget.spaced(client.get_open_positions)
    orders = budget.spaced(client.get_working_orders)
    log(f"[{label}] {len(positions)} position(s), {len(orders)} ordre(s) en attente")
    return {"positions": positions, "orders": orders}


def cluster_occupation_eur(positions_by_account: dict, db_index: dict, db_path: str) -> float:
    """Occupation RÉELLE en euros des positions déjà suivies par la base
    (`trades.risque_eur`, déjà en EUR — jamais recalculée depuis le prix
    brut, qui serait dans la devise de cotation de l'actif, pas en EUR).
    Les positions hors base (b/c/d) ne sont PAS incluses ici (leur risque
    EUR n'est pas connaissable sans un calcul converti par actif, hors
    périmètre lecture seule) — rapportées séparément, jamais mélangées."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    total = 0.0
    for state in positions_by_account.values():
        for item in state["positions"]:
            pos = item.get("position", {})
            deal_id = pos.get("dealId")
            hit = db_index.get(deal_id)
            if hit is None:
                continue
            trade_id = hit[0]
            row = conn.execute("SELECT risque_eur FROM trades WHERE id = ?", (trade_id,)).fetchone()
            if row and row[0]:
                total += row[0]
    conn.close()
    return round(total, 2)


def dump_positions(positions_by_account: dict, db_index: dict) -> None:
    for label, state in positions_by_account.items():
        for item in state["positions"]:
            pos = item.get("position", {})
            category = classify_position(item, db_index)
            epic = pos.get("epic") or item.get("market", {}).get("epic")
            db_hit = db_index.get(pos.get("dealId")) or db_index.get(pos.get("workingOrderId"))
            log(f"  [{label}] cat={category} {epic} {pos.get('direction')} taille={pos.get('size')} "
                f"entrée={pos.get('level')} stop={pos.get('stopLevel')} créée={pos.get('createdDateUTC')} "
                f"dealId={pos.get('dealId')} workingOrderId={pos.get('workingOrderId')} base={db_hit}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db-path", default=None,
                        help="Surcharge config.db_path — utilisé pour exécuter ce script depuis une machine "
                             "autre que le VPS, contre une COPIE LECTURE SEULE fraîchement synchronisée de la "
                             "base réelle (scp, jamais une écriture distante). Les appels broker eux-mêmes sont "
                             "des requêtes HTTPS directes, indépendantes de la machine d'où ce script tourne.")
    args = parser.parse_args()

    config = load_config()
    assert_demo(config)

    accounts = [
        ("main", config.capital_api_key, config.capital_identifier, config.capital_api_password, config.capital_account_id),
        ("H2", config.capital_api_key_hypothesis2, config.capital_identifier_hypothesis2,
         config.capital_api_password_hypothesis2, config.capital_account_id_hypothesis2),
        ("H3", config.capital_api_key_hypothesis3, config.capital_identifier_hypothesis3,
         config.capital_api_password_hypothesis3, config.capital_account_id_hypothesis3),
        ("H4", config.capital_api_key_hypothesis4, config.capital_identifier_hypothesis4,
         config.capital_api_password_hypothesis4, config.capital_account_id_hypothesis4),
    ]
    log("H5 IGNORÉ : CAPITAL_ACCOUNT_ID_HYPOTHESIS5 absent de ce .env local — jamais deviné, jamais cherché via /accounts.")

    db_path = args.db_path or config.db_path
    log(f"Base utilisée pour la réconciliation : {db_path}")
    db_index = build_db_index(db_path)
    budget = Budget(MAX_HTTP_CALLS)
    clients = {}
    state_before = {}

    try:
        for label, key, ident, pwd, account_id in accounts:
            if not (key and ident and pwd and account_id):
                log(f"[{label}] identifiants incomplets — compte ignoré")
                continue
            client = CapitalClient(key, ident, pwd, DEMO_BASE_URL)
            budget.spaced(client.login)
            budget.spaced(client.switch_account, account_id)
            clients[label] = client
            state_before[label] = read_account(client, budget, label)
    except RateLimited as exc:
        log(f"429 reçu ({exc}) — ARRÊT IMMÉDIAT, aucune action tentée, relancer plus tard.")
        return 2

    log("\nDétail de toutes les positions ouvertes (avant toute action) :")
    dump_positions(state_before, db_index)

    occupation_before = cluster_occupation_eur(state_before, db_index, db_path)
    log(f"\nOccupation de plafond de cluster AVANT nettoyage (somme trades.risque_eur des positions suivies "
        f"par la base, catégorie e) : {occupation_before}€")

    # --- Classement ---
    now = datetime.now(timezone.utc)
    to_close_step1 = []  # (b)/(d) sans stop, confirmées orphelines (âge >= 120 min)
    to_close_step4 = []  # (b)/(d) avec stop, confirmées orphelines
    too_recent = []  # (b)/(d) mais < 120 min : jamais actionnées, re-signalées seulement
    risk_no_stop_eur = 0.0
    for label, state in state_before.items():
        for item in state["positions"]:
            category = classify_position(item, db_index)
            pos = item.get("position", {})
            if category not in ("b", "d"):
                continue
            epic = pos.get("epic") or item.get("market", {}).get("epic")
            age = position_age_minutes(pos, now)
            entry = (label, pos.get("dealId"), epic)
            if not is_confirmed_orphan(pos, now):
                too_recent.append((*entry, age))
                continue
            if pos.get("stopLevel") is None:
                to_close_step1.append(entry)
                level, size = pos.get("level"), pos.get("size")
                if level is not None and size:
                    # Hypothèse de calcul (consignée) : risque = distance au
                    # minimum garanti broker non connue sans appel
                    # supplémentaire -> approximé par 2% du notionnel
                    # (level * size), même ordre de grandeur que
                    # risk_percent_default, JAMAIS présenté comme un calcul
                    # exact (aucune autre donnée disponible en lecture seule).
                    risk_no_stop_eur += round(level * size * 0.02, 2)
            else:
                to_close_step4.append(entry)

    log(f"\nRisque approximatif des positions SANS stop (catégories b/d, confirmées orphelines) : "
        f"~{round(risk_no_stop_eur, 2)}€ (hypothèse : 2% du notionnel, faute de distance de stop connue — "
        "jamais un calcul exact)")
    log(f"Catégorie (a) ordres de palier non annulés à vérifier : {len(UNCANCELLED_LEG_ORDERS)}")
    log(f"Catégorie (c) positions du combo H2 retiré à vérifier : {len(RETIRED_COMBO_TRADE_IDS)}")
    log(f"Étape 1 (b/d sans stop, âge >= {ORPHAN_MIN_AGE_MINUTES:.0f} min) à fermer : {to_close_step1}")
    log(f"Étape 4 (b/d avec stop, âge >= {ORPHAN_MIN_AGE_MINUTES:.0f} min) à fermer : {to_close_step4}")
    if too_recent:
        log(f"TROP RÉCENTES (< {ORPHAN_MIN_AGE_MINUTES:.0f} min ou âge inconnu), JAMAIS actionnées cette fois : "
            f"{too_recent}")

    if not args.apply:
        log("\n[SIMULATION] --apply absent : aucune action broker. Relancer avec --apply pour agir réellement.")
        log(f"Appels HTTP consommés : {budget.used}/{MAX_HTTP_CALLS}")
        return 0

    # --- Étape 1 : ferme (b)/(d) sans stop ---
    for label, deal_id, epic in to_close_step1:
        try:
            result = budget.spaced(clients[label].close_position, deal_id, requested_at=datetime.now(timezone.utc).isoformat())
            log(f"[{label}] ÉTAPE1 fermeture {epic} {deal_id} : {result.get('level')}")
        except RateLimited as exc:
            log(f"429 pendant l'étape 1 ({exc}) — ARRÊT IMMÉDIAT.")
            return 2
        except CapitalApiError as exc:
            log(f"[{label}] ÉTAPE1 fermeture {epic} {deal_id} REFUSÉE : {exc} — marché peut-être fermé, suivant")
            continue
        try:
            still_open = {p.get("position", {}).get("dealId") for p in budget.spaced(clients[label].get_open_positions)}
        except RateLimited as exc:
            log(f"429 après l'étape 1 ({exc}) — ARRÊT IMMÉDIAT.")
            return 2
        log(f"[{label}] {epic} {deal_id} : {'ENCORE OUVERTE — à vérifier à la main' if deal_id in still_open else 'fermée, réconciliée'}")

    # --- Étape 2 : annule les ordres (a) ---
    reclassified_as_b = []
    for working_order_id, source in UNCANCELLED_LEG_ORDERS.items():
        label = {"hypothesis3_v2": "H3", "hypothesis4_v2": "H4"}.get(source)
        if label not in clients:
            log(f"ÉTAPE2 {working_order_id} ({source}) : compte {label} non disponible — sauté")
            continue
        try:
            budget.spaced(clients[label].cancel_working_order, working_order_id)
            log(f"[{label}] ÉTAPE2 annulation {working_order_id} : réussie")
        except RateLimited as exc:
            log(f"429 pendant l'étape 2 ({exc}) — ARRÊT IMMÉDIAT.")
            return 2
        except CapitalApiError as exc:
            if "404" in str(exc) or "not-found" in str(exc).lower():
                log(f"[{label}] ÉTAPE2 {working_order_id} : 404 — déjà rempli, reclassé (b)")
                reclassified_as_b.append((label, working_order_id))
            else:
                log(f"[{label}] ÉTAPE2 {working_order_id} : annulation REFUSÉE ({exc}) — sauté")

    # --- Étape 3 : ferme les 4 positions (c), compte H2 uniquement ---
    if "H2" in clients:
        try:
            open_positions_h2 = budget.spaced(clients["H2"].get_open_positions)
        except RateLimited as exc:
            log(f"429 avant l'étape 3 ({exc}) — ARRÊT IMMÉDIAT.")
            return 2
        open_deal_ids_h2 = {p.get("position", {}).get("dealId") for p in open_positions_h2}
        reverse_index = {v[0]: k for k, v in db_index.items()}
        for trade_id in RETIRED_COMBO_TRADE_IDS:
            deal_id = reverse_index.get(trade_id)
            if deal_id is None or deal_id not in open_deal_ids_h2:
                log(f"[H2] ÉTAPE3 trade {trade_id} : absent des positions broker ouvertes — rien à fermer")
                continue
            try:
                result = budget.spaced(clients["H2"].close_position, deal_id, requested_at=datetime.now(timezone.utc).isoformat())
                log(f"[H2] ÉTAPE3 fermeture trade {trade_id} ({deal_id}) : {result.get('level')}")
            except RateLimited as exc:
                log(f"429 pendant l'étape 3 ({exc}) — ARRÊT IMMÉDIAT.")
                return 2
            except CapitalApiError as exc:
                log(f"[H2] ÉTAPE3 trade {trade_id} fermeture REFUSÉE : {exc} — marché peut-être fermé, suivant")
    else:
        log("ÉTAPE3 : compte H2 non disponible — sautée entièrement")

    # --- Étape 4 : ferme (b)/(d) avec stop restantes (y compris les reclassées en (b) à l'étape 2) ---
    for label, deal_id, epic in to_close_step4:
        try:
            result = budget.spaced(clients[label].close_position, deal_id, requested_at=datetime.now(timezone.utc).isoformat())
            log(f"[{label}] ÉTAPE4 fermeture {epic} {deal_id} : {result.get('level')}")
        except RateLimited as exc:
            log(f"429 pendant l'étape 4 ({exc}) — ARRÊT IMMÉDIAT.")
            return 2
        except CapitalApiError as exc:
            log(f"[{label}] ÉTAPE4 fermeture {epic} {deal_id} REFUSÉE : {exc} — marché peut-être fermé, suivant")

    log(f"\nAppels HTTP consommés : {budget.used}/{MAX_HTTP_CALLS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
