"""
calibrate_pip_value.py — Calibration EMPIRIQUE de pip_value_per_unit par
mesure réelle sur le compte DÉMO Capital.com (jamais le compte réel OANDA,
jamais touché par ce projet).

Pourquoi : src/asset_whitelist.py calcule pip_value_per_unit via un taux de
change spot USD/EUR figé au 16/08/2026, faute de formule écrite dans le
CDC v4 (indisponible dans ce dépôt). C'est une approximation d'ingénieur,
pas une mesure. Ce script la vérifie contre la réalité du compte.

Méthode, pour chaque actif tradable au moment de l'exécution :
  1. Lire le solde du compte démo (avant).
  2. Ouvrir une position LONG à minDealSize (taille minimale autorisée).
  3. Noter le prix d'entrée (niveau confirmé par l'API) et l'heure.
  4. Attendre ~60s (mouvement de marché naturel).
  5. Fermer IMMÉDIATEMENT la position (toujours, même en cas d'erreur —
     voir le bloc finally : ne jamais laisser une position ouverte sans
     contrôle).
  6. Relire le solde du compte (après). Le delta = P&L réalisé en EUR
     (devise du compte), spread inclus — c'est la vraie valeur qui compte,
     pas une estimation théorique.
  7. pip_value_per_unit empirique = P&L réalisé (EUR) / mouvement de prix
     observé (prix de sortie - prix d'entrée) / taille.
  8. Comparer à la valeur codée en dur dans src/asset_whitelist.py. Si
     l'écart relatif dépasse 20%, l'AFFICHER CLAIREMENT SANS RIEN CORRIGER
     automatiquement (invariant #10 : toute variable financière critique
     doit être validée par un humain avant modification).

Actifs 24/7 (BTCUSD, ETHUSD) : mesurés à chaque exécution.

Actifs 24/5 (GOLD, US100, US30) — TODO daté 16/08/2026 : le marché est
fermé le week-end (confirmé lors de l'extraction du 16/08/2026, un
dimanche). Ce script les inclut déjà dans TARGETS et les mesurera
automatiquement dès qu'ils repasseront TRADEABLE (dimanche soir tard /
lundi selon les horaires réels Capital.com pour chaque actif — vérifier au
moment de relancer). Aucune action requise : relancer simplement
`python calibrate_pip_value.py` un jour ouvré.

Sécurité : taille = minDealSize (quelques centimes de risque max sur
capital démo), position fermée en quelques dizaines de secondes, jamais de
levier ou de taille au-delà du minimum. N'écrit jamais dans
src/asset_whitelist.py — le script ne fait que rapporter.

Usage : python calibrate_pip_value.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.asset_whitelist import ASSET_WHITELIST

load_dotenv()

API_KEY = os.environ.get("CAPITAL_API_KEY")
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER")
PASSWORD = os.environ.get("CAPITAL_API_PASSWORD")
BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

WAIT_SECONDS = 60
DISCREPANCY_THRESHOLD = 0.20  # 20%

# Le solde du compte démo est arrondi (constaté : un P&L théorique de
# -0.005 EUR à minDealSize s'est affiché comme un delta de solde
# EXACTEMENT nul, rendant toute mesure impossible). On utilise donc une
# taille de test dimensionnée sur un budget de marge fixe — assez grande
# pour qu'un mouvement de marché normal sur 60s produise un P&L visible,
# assez petite pour rester une fraction mineure du compte démo (1000€).
TEST_MARGIN_BUDGET_EUR = 30.0

# epic Capital.com -> clé dans ASSET_WHITELIST (src/asset_whitelist.py)
TARGETS = {
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
    "GOLD": "GOLD",
    "US100": "US100",
    "US30": "US30",
}

OUTPUT_PATH = Path("data") / "pip_value_calibration.json"

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
    resp = requests.get(f"{BASE_URL}{path}", headers={"X-CAP-API-KEY": API_KEY, **tokens}, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def post(path, tokens, body):
    resp = requests.post(
        f"{BASE_URL}{path}",
        headers={"X-CAP-API-KEY": API_KEY, **tokens, "Content-Type": "application/json"},
        json=body, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def delete(path, tokens, body=None):
    resp = requests.delete(
        f"{BASE_URL}{path}",
        headers={"X-CAP-API-KEY": API_KEY, **tokens, "Content-Type": "application/json"},
        json=body, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_account_balance_eur(tokens):
    accounts = get("/accounts", tokens)
    for acc in accounts.get("accounts", []):
        if acc.get("preferred"):
            currency = acc.get("currency")
            balance = acc.get("balance", {}).get("balance")
            if currency != "EUR":
                print(f"   [avertissement] devise du compte démo = {currency}, pas EUR. "
                      f"Le delta de solde ne sera pas directement en EUR.")
            return balance, currency
    raise RuntimeError("Aucun compte 'preferred' trouvé dans la réponse /accounts")


def compute_test_size(instrument, dealing_rules, reference_price, budget_eur):
    """
    Taille de test = budget_eur de marge convertie en taille de position via
    marginFactor (% du notionnel exigé en marge). Arrondie au multiple de
    minSizeIncrement, bornée par [minDealSize, maxDealSize]. Si marginFactor
    est indisponible, repli sur minDealSize (moins précis mais sûr).
    """
    min_deal_size = dealing_rules.get("minDealSize", {}).get("value")
    margin_factor = instrument.get("marginFactor")

    if not margin_factor or not reference_price or not min_deal_size:
        return min_deal_size, "minDealSize (marginFactor ou prix indisponible)"

    max_deal_size = dealing_rules.get("maxDealSize", {}).get("value")
    increment = dealing_rules.get("minSizeIncrement", {}).get("value") or min_deal_size

    notional_budget = budget_eur / (margin_factor / 100.0)
    raw_size = notional_budget / reference_price
    steps = max(1, int(raw_size / increment))
    size = round(steps * increment, 10)
    size = max(size, min_deal_size)
    if max_deal_size:
        size = min(size, max_deal_size)
    return size, f"budget_eur={budget_eur}, marginFactor={margin_factor}%"


def find_open_position(tokens, deal_id):
    positions = get("/positions", tokens)
    for entry in positions.get("positions", []):
        if entry.get("position", {}).get("dealId") == deal_id:
            return entry
    return None


def close_position(tokens, deal_id, size, direction_opened):
    closing_direction = "SELL" if direction_opened == "BUY" else "BUY"
    try:
        # Pas de "size" dans le body : une fermeture sans taille ferme la
        # position entière (vérifié manuellement — passer size a provoqué
        # un 404 dealId lors de la mise au point de ce script).
        result = delete(f"/positions/{deal_id}", tokens)
        print(f"   Position {deal_id} fermée : {result}")
        return True
    except requests.HTTPError as exc:
        print(f"   [ERREUR CRITIQUE] Échec de fermeture de {deal_id} via DELETE /positions/{deal_id} : {exc}")
        print(f"   >>> ACTION MANUELLE REQUISE : fermez la position {deal_id} (epic direction={closing_direction}, "
              f"taille={size}) via l'app/le site Capital.com démo IMMÉDIATEMENT. <<<")
        return False


def calibrate_one(epic, whitelist_key, tokens):
    print(f"\n=== {epic} ===")
    market = get(f"/markets/{epic}", tokens)
    instrument = market.get("instrument", {})
    snapshot = market.get("snapshot", {})
    dealing_rules = market.get("dealingRules", {})
    market_status = snapshot.get("marketStatus")

    if market_status != "TRADEABLE":
        print(f"   Marché fermé actuellement (statut={market_status}) — ignoré. "
              f"Relancer ce script quand {epic} repasse TRADEABLE.")
        return {"epic": epic, "skipped": True, "reason": f"market_status={market_status}"}

    min_deal_size = dealing_rules.get("minDealSize", {}).get("value")
    if not min_deal_size:
        print("   minDealSize introuvable, ignoré.")
        return {"epic": epic, "skipped": True, "reason": "minDealSize introuvable"}

    reference_price = snapshot.get("offer")
    test_size, sizing_note = compute_test_size(instrument, dealing_rules, reference_price, TEST_MARGIN_BUDGET_EUR)
    print(f"   Taille de test : {test_size} (minDealSize={min_deal_size}, {sizing_note})")

    balance_before, currency = get_account_balance_eur(tokens)
    print(f"   Solde compte avant : {balance_before} {currency}")

    open_body = {"epic": epic, "direction": "BUY", "size": test_size}

    # Ce compte démo exige un stop garanti sur certains instruments
    # (erreur API "guaranteed-stop-loss.required" constatée sur les
    # cryptos lors de la mise au point de ce script). On le fournit
    # systématiquement quand dealingRules l'indique, avec une marge de
    # sécurité de 50% au-dessus du minimum pour ne jamais être rejeté ni
    # déclenché sur la fenêtre de mesure de 60s. NB : un stop garanti peut
    # entraîner une prime/coût facturé par Capital.com, ce qui peut biaiser
    # le P&L mesuré (voir avertissement en fin de mesure).
    min_guaranteed_stop = dealing_rules.get("minGuaranteedStopDistance", {})
    guaranteed_stop_used = False
    if min_guaranteed_stop.get("value") and min_guaranteed_stop.get("unit") == "PERCENTAGE":
        min_distance = reference_price * (min_guaranteed_stop["value"] / 100.0)
        stop_distance = round(min_distance * 1.5, 8)
        open_body["guaranteedStop"] = True
        open_body["stopDistance"] = stop_distance
        guaranteed_stop_used = True
        print(f"   Stop garanti requis par ce compte : stopDistance={stop_distance} "
              f"(minimum API={min_distance:.6f})")

    print(f"   Ouverture position LONG {test_size} {epic} @ ~{snapshot.get('offer')} ...")
    open_resp = post("/positions", tokens, open_body)
    deal_reference = open_resp.get("dealReference")
    if not deal_reference:
        print(f"   [ERREUR] Pas de dealReference retourné : {open_resp}")
        return {"epic": epic, "skipped": True, "reason": "ouverture échouée (pas de dealReference)"}

    confirmation = get(f"/confirms/{deal_reference}", tokens)
    deal_status = confirmation.get("dealStatus")
    entry_level = confirmation.get("level")
    opened_at = datetime.now(timezone.utc)

    # IMPORTANT : le "dealId" au premier niveau de /confirms est l'ID de
    # l'ORDRE, pas de la POSITION (constaté lors de la mise au point — un
    # DELETE /positions/{ce dealId} renvoie 404). Le vrai dealId de
    # position est dans affectedDeals[0].dealId.
    affected = confirmation.get("affectedDeals") or []
    deal_id = affected[0]["dealId"] if affected else None

    if deal_status != "ACCEPTED" or deal_id is None:
        print(f"   [ERREUR] Ouverture refusée ou incomplète : {confirmation}")
        return {"epic": epic, "skipped": True, "reason": f"deal_status={deal_status}"}

    print(f"   Position ouverte : dealId={deal_id}, entry_level={entry_level}, heure={opened_at.isoformat()}")

    closed_successfully = False
    try:
        print(f"   Attente {WAIT_SECONDS}s ...")
        time.sleep(WAIT_SECONDS)

        position_entry = find_open_position(tokens, deal_id)
        if position_entry is None:
            print(f"   [ERREUR] Position {deal_id} introuvable dans /positions au moment de la mesure.")
            return {"epic": epic, "skipped": True, "reason": "position introuvable pour la mesure"}

        current_market = position_entry.get("market", {})
        exit_level = current_market.get("bid")  # prix de sortie réel pour un long
        live_upl = position_entry.get("position", {}).get("upl")  # diagnostic uniquement
        measured_at = datetime.now(timezone.utc)
        print(f"   Mesure à {measured_at.isoformat()} : bid courant={exit_level}, upl (diagnostic)={live_upl}")

    finally:
        closed_successfully = close_position(tokens, deal_id, test_size, "BUY")

    if not closed_successfully:
        return {
            "epic": epic, "skipped": False, "closed": False,
            "deal_id": deal_id,
            "warning": "POSITION POTENTIELLEMENT ENCORE OUVERTE — vérifier manuellement sur Capital.com",
        }

    balance_after, _ = get_account_balance_eur(tokens)
    realized_pnl = round(balance_after - balance_before, 6)
    price_movement = exit_level - entry_level if (exit_level is not None and entry_level is not None) else None

    print(f"   Solde compte après : {balance_after} {currency}")
    print(f"   P&L réalisé (solde après - avant) : {realized_pnl} {currency}")
    print(f"   Mouvement de prix observé (bid sortie - level entrée) : {price_movement}")

    if guaranteed_stop_used and live_upl is not None:
        gap = round(realized_pnl - live_upl, 6)
        if abs(gap) > 0.001:
            print(f"   [avertissement] Écart entre upl en direct ({live_upl}) et P&L réalisé "
                  f"({realized_pnl}) = {gap} {currency} — probablement une prime de stop garanti "
                  f"ou des frais, indépendants du mouvement de prix. Le calibrage ci-dessous peut "
                  f"être biaisé par ce montant.")

    if not price_movement or realized_pnl == 0:
        print("   [avertissement] Mouvement de prix nul ou trop faible sur la fenêtre de mesure — "
              "résultat peu fiable, à refaire.")
        empirical_pip_value = None
    else:
        empirical_pip_value = realized_pnl / price_movement / test_size

    coded_spec = ASSET_WHITELIST.get(whitelist_key)
    coded_pip_value = coded_spec.pip_value_per_unit if coded_spec else None

    result = {
        "epic": epic,
        "skipped": False,
        "closed": True,
        "min_deal_size": min_deal_size,
        "test_size": test_size,
        "entry_level": entry_level,
        "exit_level": exit_level,
        "price_movement": price_movement,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "realized_pnl": realized_pnl,
        "account_currency": currency,
        "guaranteed_stop_used": guaranteed_stop_used,
        "live_upl_diagnostic": live_upl,
        "empirical_pip_value_per_unit": empirical_pip_value,
        "coded_pip_value_per_unit": coded_pip_value,
        "measured_at_utc": measured_at.isoformat(),
    }

    if empirical_pip_value is not None and coded_pip_value:
        discrepancy = abs(empirical_pip_value - coded_pip_value) / coded_pip_value
        result["discrepancy_pct"] = round(discrepancy * 100, 2)
        print(f"   pip_value_per_unit empirique : {empirical_pip_value:.8f}")
        print(f"   pip_value_per_unit codé (asset_whitelist.py) : {coded_pip_value:.8f}")
        print(f"   Écart relatif : {discrepancy * 100:.2f}%")
        if discrepancy > DISCREPANCY_THRESHOLD:
            print(f"   >>> ÉCART > {DISCREPANCY_THRESHOLD * 100:.0f}% : src/asset_whitelist.py DEVRAIT ÊTRE "
                  f"CORRIGÉ MANUELLEMENT pour {epic}. Ce script ne le fait PAS automatiquement. <<<")
        else:
            print(f"   Écart sous le seuil de {DISCREPANCY_THRESHOLD * 100:.0f}% — valeur codée jugée correcte.")

    return result


def main():
    print("1) Ouverture de session Capital.com (démo)...")
    tokens = create_session()
    print("   OK.")

    results = {}
    for epic, whitelist_key in TARGETS.items():
        try:
            results[epic] = calibrate_one(epic, whitelist_key, tokens)
        except requests.HTTPError as exc:
            print(f"   [ERREUR HTTP] {epic} : {exc}")
            results[epic] = {"epic": epic, "skipped": True, "reason": str(exc)}

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"run_at_utc": datetime.now(timezone.utc).isoformat(), "results": results},
            f, ensure_ascii=False, indent=2,
        )
    print(f"\nRésultats écrits dans {OUTPUT_PATH}.")

    warnings = [r for r in results.values() if r.get("warning")]
    if warnings:
        print("\n!!! ATTENTION : au moins une position n'a peut-être pas été fermée. "
              "Vérifiez manuellement le compte démo Capital.com. !!!")


if __name__ == "__main__":
    main()
