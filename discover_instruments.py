"""
discover_instruments.py — Découverte des specs d'instruments via l'API
Capital.com (démo), en lecture seule. Aucun ordre n'est passé.

Objectif : remplacer le remplissage manuel du tableau §1.2 du CDC v4 par une
extraction reproductible des specs réelles (epic, taille minimale, lotSize,
distances de stop minimales, statut de marché) pour les actifs visés :
XAUUSD/GOLD, NAS100/US100, US30, EUR/USD, GBP/USD, USD/JPY, BTC/USD, ETH/USD.

Ce script ne calcule PAS pip_value_per_unit ni n'applique la règle de
décision (risque plancher = taille min x valeur pip x 30 pips > 10€ => hors
liste blanche). Ce calcul dépend de la formule exacte du CDC v4 (conversion
de devise, définition précise de "valeur pip" pour chaque classe d'actif -
FX / métaux / indices / crypto) et ne doit pas être inventé ici (invariant
#10 : toute variable/formule doit être justifiée par écrit avant de
regarder les données). Il produit les données brutes nécessaires pour que
cette décision soit prise en connaissance de cause.

Sortie :
- Tableau lisible en console (français)
- data/instrument_specs.json : dump brut horodaté pour traçabilité
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("CAPITAL_API_KEY")
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER")
PASSWORD = os.environ.get("CAPITAL_API_PASSWORD")

BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

# CDC v4 §1.2 : actifs visés. Chaque entrée liste les termes de recherche à
# essayer dans l'ordre jusqu'à obtenir des résultats.
TARGET_ASSETS = {
    "XAUUSD/GOLD": ["GOLD", "XAUUSD"],
    "NAS100/US100": ["US100", "NAS100"],
    "US30": ["US30"],
    "EUR/USD": ["EURUSD"],
    "GBP/USD": ["GBPUSD"],
    "USD/JPY": ["USDJPY"],
    "BTC/USD": ["BTCUSD"],
    "ETH/USD": ["ETHUSD"],
}

if not all([API_KEY, IDENTIFIER, PASSWORD]):
    print("ERREUR : CAPITAL_API_KEY, CAPITAL_IDENTIFIER ou CAPITAL_API_PASSWORD manquant dans .env")
    sys.exit(1)


def create_session():
    resp = requests.post(
        f"{BASE_URL}/session",
        headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
        json={"identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False},
        timeout=10,
    )
    resp.raise_for_status()
    return {"CST": resp.headers["CST"], "X-SECURITY-TOKEN": resp.headers["X-SECURITY-TOKEN"]}


def get(path, tokens, params=None):
    resp = requests.get(
        f"{BASE_URL}{path}",
        headers={"X-CAP-API-KEY": API_KEY, **tokens},
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def search_markets(tokens, terms):
    """Essaie chaque terme de recherche jusqu'à obtenir des résultats."""
    for term in terms:
        try:
            body = get("/markets", tokens, params={"searchTerm": term})
        except requests.HTTPError as exc:
            print(f"   [avertissement] recherche '{term}' échouée : {exc}")
            continue
        markets = body.get("markets", [])
        if markets:
            return term, markets
    return terms[-1], []


def is_weekend_epic(epic: str) -> bool:
    return epic.upper().endswith("_W")


def fetch_details(tokens, epic):
    try:
        return get(f"/markets/{epic}", tokens)
    except requests.HTTPError as exc:
        print(f"   [avertissement] détails '{epic}' indisponibles : {exc}")
        return None


def main():
    print("1) Ouverture de session Capital.com (démo)...")
    tokens = create_session()
    print("   OK.\n")

    results = {}

    for label, terms in TARGET_ASSETS.items():
        print(f"=== {label} ===")
        used_term, markets = search_markets(tokens, terms)
        if not markets:
            print(f"   Aucun résultat pour les termes essayés ({', '.join(terms)}).\n")
            results[label] = {"search_term": used_term, "candidates": [], "primary": None}
            continue

        candidates = []
        for m in markets:
            epic = m.get("epic", "?")
            candidates.append({
                "epic": epic,
                "instrumentName": m.get("instrumentName"),
                "instrumentType": m.get("instrumentType"),
                "marketStatus": m.get("marketStatus"),
                "weekend_synthetic": is_weekend_epic(epic),
            })

        print(f"   Candidats trouvés (terme '{used_term}') :")
        for c in candidates:
            flag = "  [MARCHÉ WEEK-END SYNTHÉTIQUE - À NE PAS UTILISER SANS VALIDATION]" if c["weekend_synthetic"] else ""
            print(f"     - epic={c['epic']:<15} nom={c['instrumentName']:<25} statut={c['marketStatus']}{flag}")

        # Candidat principal = premier non "_W". On ne retombe jamais sur un
        # epic _W automatiquement (cf. instructions projet).
        primary_candidate = next((c for c in candidates if not c["weekend_synthetic"]), None)

        primary_details = None
        raw_details = None
        if primary_candidate:
            raw_details = fetch_details(tokens, primary_candidate["epic"])
            if raw_details:
                instrument = raw_details.get("instrument", {})
                dealing_rules = raw_details.get("dealingRules", {})
                snapshot = raw_details.get("snapshot", {})

                min_deal_size = dealing_rules.get("minDealSize", {})
                min_step = dealing_rules.get("minStepDistance", {})
                min_ctrl_risk_stop = dealing_rules.get("minControlledRiskStopDistance", {})
                min_normal_stop = dealing_rules.get("minNormalStopOrLimitDistance", {})

                primary_details = {
                    "epic": primary_candidate["epic"],
                    "instrumentName": instrument.get("name"),
                    "type": instrument.get("type"),
                    "lotSize": instrument.get("lotSize"),
                    "contractSize": instrument.get("contractSize"),
                    "currencies": instrument.get("currencies"),
                    "marginFactor": instrument.get("marginFactor"),
                    "marginFactorUnit": instrument.get("marginFactorUnit"),
                    "marketId": instrument.get("marketId"),
                    "minDealSize_value": min_deal_size.get("value"),
                    "minDealSize_unit": min_deal_size.get("unit"),
                    "minStepDistance_value": min_step.get("value"),
                    "minStepDistance_unit": min_step.get("unit"),
                    "minControlledRiskStopDistance": min_ctrl_risk_stop,
                    "minNormalStopOrLimitDistance": min_normal_stop,
                    "marketStatus": snapshot.get("marketStatus"),
                    "bid": snapshot.get("bid"),
                    "offer": snapshot.get("offer"),
                    "decimalPlacesFactor": snapshot.get("decimalPlacesFactor"),
                    "scalingFactor": instrument.get("scalingFactor") or snapshot.get("scalingFactor"),
                }

                print(f"\n   Détails ({primary_candidate['epic']}) :")
                for k, v in primary_details.items():
                    print(f"     {k}: {v}")

        print()
        results[label] = {
            "search_term": used_term,
            "candidates": candidates,
            "primary": primary_details,
            "primary_raw": raw_details,
        }

    out_dir = os.path.join("data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "instrument_specs.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "assets": results},
            f, ensure_ascii=False, indent=2,
        )

    print(f"Terminé. Dump brut écrit dans {out_path}.")
    print(
        "\nRAPPEL : ce script ne calcule PAS pip_value_per_unit ni n'applique la\n"
        "règle de décision du §1.2 (risque plancher = taille min x valeur pip x\n"
        "30 pips > 10€ => hors liste blanche). Cette formule dépend de la\n"
        "définition exacte du CDC v4 (conversion de devise selon la classe\n"
        "d'actif) et doit être validée avant d'être appliquée aux AssetSpec de\n"
        "risk_engine.py (invariant #10)."
    )


if __name__ == "__main__":
    main()
