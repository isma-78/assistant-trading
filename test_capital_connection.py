"""
test_capital_connection.py — Vérification de connexion à l'API Capital.com
(démo). Lecture seule : solde du compte + un prix. Aucun ordre n'est passé.
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("CAPITAL_API_KEY")
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER")
PASSWORD = os.environ.get("CAPITAL_API_PASSWORD")

BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

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
    print(f"POST /session -> {resp.status_code}")
    resp.raise_for_status()
    tokens = {"CST": resp.headers["CST"], "X-SECURITY-TOKEN": resp.headers["X-SECURITY-TOKEN"]}
    return tokens, resp.json()


def get(path, tokens, params=None):
    resp = requests.get(
        f"{BASE_URL}{path}",
        headers={"X-CAP-API-KEY": API_KEY, **tokens},
        params=params,
        timeout=10,
    )
    print(f"GET {path} -> {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def main():
    print("1) Ouverture de session...")
    tokens, session_body = create_session()
    print("   Réponse brute :", session_body)

    print("\n2) Récupération des comptes / solde...")
    accounts = get("/accounts", tokens)
    print("   Réponse brute :", accounts)

    print("\n3) Recherche d'un prix (EUR/USD)...")
    markets = get("/markets", tokens, params={"searchTerm": "EURUSD"})
    print("   Réponse brute :", markets)

    print("\nTest terminé. Aucun ordre n'a été passé.")


if __name__ == "__main__":
    main()