"""
_verify_leg_independence.py — Vérification PONCTUELLE (préfixe _), 25/09/2026,
avant le déploiement du correctif TP1 « Option B » (trois positions dès
l'entrée, voir docs/DECISIONS.md) : prouve sur l'API réelle (compte démo
"hypothèse 4", faute de sous-compte libre — voir docs/DECISIONS.md) que
trois ordres/positions du même actif et du même sens se comportent
indépendamment :

  A. 3 ordres limite (loin du marché, jamais remplis) : annuler l'un
     laisse les deux autres intacts.
  B. 3 positions EURUSD (taille minimale) : modifier le stop de l'une ne
     touche pas les autres ; fermer l'une (DELETE, sans taille) laisse les
     deux autres ouvertes, taille intacte.
  C. Idem sur ETHUSD.
Stop garanti partout : les 9 actifs portent une règle
`minGuaranteedStopDistance` sur ce compte (vérifié le 25/09/2026) — la
production pose donc toujours un stop garanti, le test fait de même.

Taille minimale broker partout, positions fermées dans la minute. Tout
dealId ouvert par ce script est fermé/annulé dans le `finally`, quoi qu'il
arrive. Aucune écriture en base. Sortie JSON de preuve dans logs/.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalClient
from src.config import load_config

_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
_PAUSE = 1.2
_OUT = Path(__file__).resolve().parent.parent / "logs" / "leg_independence_test_25-09-2026.json"

evidence = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
opened_positions: set = set()
placed_orders: set = set()


def log(step, ok, **data):
    evidence["steps"].append({"step": step, "ok": ok, **data})
    print(f"[{'OK' if ok else 'ECHEC'}] {step} {json.dumps(data, ensure_ascii=False, default=str)}")


def positions_by_id(client):
    return {p["position"]["dealId"]: p["position"] for p in client.get_open_positions()}


def order_ids(client):
    return {o["workingOrderData"]["dealId"] for o in client.get_working_orders()}


def guaranteed_distance(market, reference_price):
    rule = market["dealingRules"]["minGuaranteedStopDistance"]
    raw = reference_price * rule["value"] / 100.0 if rule.get("unit") == "PERCENTAGE" else rule["value"]
    return raw * 1.5


def part_a(client):
    market = client.get_market_snapshot("EURUSD")
    level = round(market["snapshot"]["bid"] * 0.97, 5)
    distance = round(guaranteed_distance(market, level), 5)
    ids = []
    for _ in range(3):
        ids.append(client.place_limit_order("EURUSD", "BUY", 100, level, guaranteed_stop=True, stop_distance=distance)["deal_id"])
        placed_orders.add(ids[-1])
        time.sleep(_PAUSE)
    live = order_ids(client)
    log("A1 trois ordres limite distincts", len(set(ids)) == 3 and set(ids) <= live, ids=ids, level=level)
    client.cancel_working_order(ids[0]); placed_orders.discard(ids[0]); time.sleep(_PAUSE)
    live = order_ids(client)
    log("A2 annuler l'ordre 1 laisse 2 et 3", ids[0] not in live and ids[1] in live and ids[2] in live)
    for i in ids[1:]:
        client.cancel_working_order(i); placed_orders.discard(i); time.sleep(_PAUSE)
    log("A3 nettoyage ordres", not (set(ids) & order_ids(client)))


def part_positions(client, label, epic, size, decimals):
    market = client.get_market_snapshot(epic)
    stop_distance = round(guaranteed_distance(market, market["snapshot"]["bid"]), decimals)
    guaranteed = True
    ids = []
    for _ in range(3):
        ids.append(client.open_position(epic, "BUY", size, guaranteed_stop=guaranteed, stop_distance=stop_distance)["deal_id"])
        opened_positions.add(ids[-1])
        time.sleep(_PAUSE)
    pos = positions_by_id(client)
    before = {i: {"size": pos[i]["size"], "stop": pos[i].get("stopLevel")} for i in ids if i in pos}
    log(f"{label}1 trois positions distinctes", len(before) == 3 and all(v["size"] == size for v in before.values()), positions=before)

    new_stop = round(before[ids[0]]["stop"] + stop_distance * 0.2, decimals)  # resserrement (long : on remonte)
    client.update_position_stop(ids[0], new_stop, guaranteed_stop=guaranteed, direction="long",
                                current_stop_level=before[ids[0]]["stop"])
    time.sleep(_PAUSE)
    pos = positions_by_id(client)
    after = {i: pos[i].get("stopLevel") for i in ids}
    log(f"{label}2 stop modifié sur la position 1 seulement",
        after[ids[0]] != before[ids[0]]["stop"] and after[ids[1]] == before[ids[1]]["stop"] and after[ids[2]] == before[ids[2]]["stop"],
        stops_avant={i: before[i]["stop"] for i in ids}, stops_apres=after, demande=new_stop)

    client.close_position(ids[0]); opened_positions.discard(ids[0]); time.sleep(_PAUSE)
    pos = positions_by_id(client)
    log(f"{label}3 fermer la position 1 laisse 2 et 3 intactes",
        ids[0] not in pos and ids[1] in pos and ids[2] in pos and pos[ids[1]]["size"] == size and pos[ids[2]]["size"] == size,
        restantes={i: pos[i]["size"] for i in ids if i in pos})
    for i in ids[1:]:
        client.close_position(i); opened_positions.discard(i); time.sleep(_PAUSE)
    log(f"{label}4 nettoyage positions", not (set(ids) & set(positions_by_id(client))))


def main():
    config = load_config()
    if config.capital_environment != "demo":
        sys.exit("REFUS : environnement non démo.")
    client = CapitalClient(config.capital_api_key_hypothesis4, config.capital_identifier_hypothesis4,
                           config.capital_api_password_hypothesis4, _DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id_hypothesis4)
    evidence["account"] = "hypothèse 4"
    try:
        part_a(client)
        part_positions(client, "B", "EURUSD", 100, decimals=5)
        part_positions(client, "C", "ETHUSD", 0.001, decimals=2)
    except Exception as exc:
        log("EXCEPTION", False, error=str(exc))
    finally:
        for i in list(opened_positions):
            try:
                client.close_position(i)
                log("nettoyage de secours position", True, deal_id=i)
            except Exception as exc:
                log("nettoyage de secours position", False, deal_id=i, error=str(exc))
        for i in list(placed_orders):
            try:
                client.cancel_working_order(i)
                log("nettoyage de secours ordre", True, deal_id=i)
            except Exception as exc:
                log("nettoyage de secours ordre", False, deal_id=i, error=str(exc))
        evidence["all_ok"] = all(s["ok"] for s in evidence["steps"])
        _OUT.parent.mkdir(exist_ok=True)
        _OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nRESULTAT GLOBAL : {'TOUT OK' if evidence['all_ok'] else 'ECHEC'} — preuve : {_OUT}")


if __name__ == "__main__":
    main()
