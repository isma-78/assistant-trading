"""Test d'intégration DÉMO (29/10/2026) : place UN ordre STOP d'entrée à taille
minimale, loin du prix, avec son stop garanti attaché ; vérifie qu'il est
accepté, présent côté broker avec son stop, puis l'annule et vérifie
l'annulation. Compte démo de l'hypothèse 5 uniquement. Aucun ordre au marché.
Usage : python scripts/test_integration_ordre_stop.py [EPIC] [buy|sell]"""
import json
import sys
import time

sys.path.insert(0, ".")
from src.capital_client import CapitalApiError, CapitalClient
from src.config import load_config

DEMO = "https://demo-api-capital.backend-capital.com/api/v1"


def main() -> int:
    epic = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    side = sys.argv[2] if len(sys.argv) > 2 else "buy"
    config = load_config()
    if config.capital_environment != "demo":
        print("REFUS : environnement non démo")
        return 2
    client = CapitalClient(config.capital_api_key_hypothesis5, config.capital_identifier_hypothesis5,
                           config.capital_api_password_hypothesis5, DEMO)
    client.login()
    if config.capital_account_id_hypothesis5:
        client.switch_account(config.capital_account_id_hypothesis5)
    market = client.get_market_snapshot(epic)
    snap, rules = market["snapshot"], market.get("dealingRules", {})
    bid, ask = snap["bid"], snap["offer"]
    min_size = rules.get("minDealSize", {}).get("value", 1)
    gs = rules.get("minGuaranteedStopDistance", {})
    print("bid/ask", bid, ask, "minDealSize", min_size, "minGuaranteedStopDistance", gs)
    if side == "buy":
        direction, level = "BUY", round(ask * 1.03, 5)
    else:
        direction, level = "SELL", round(bid * 0.97, 5)
    raw_min = level * gs.get("value", 0) / 100 if gs.get("unit") == "PERCENTAGE" else gs.get("value", 0)
    stop_distance = round(max(raw_min * 1.5, level * 0.01), 5)
    order = None
    try:
        order = client.place_stop_entry_order(epic, direction, min_size, level, True, stop_distance)
        print("ACCEPTÉ", json.dumps(order["confirmation"], default=str)[:600])
        time.sleep(1)
        found = [w for w in client.get_working_orders() if w.get("workingOrderData", {}).get("dealId") == order["deal_id"]]
        print("présent côté broker :", bool(found))
        if found:
            print(json.dumps(found[0], default=str)[:700])
    except (CapitalApiError, Exception) as exc:
        print("REFUSÉ/ERREUR :", repr(exc)[:800])
        return 1
    finally:
        if order:
            client.cancel_working_order(order["deal_id"])
            time.sleep(1)
            still = [w for w in client.get_working_orders() if w.get("workingOrderData", {}).get("dealId") == order["deal_id"]]
            print("annulé :", not still)
    return 0


if __name__ == "__main__":
    sys.exit(main())
