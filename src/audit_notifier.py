"""
audit_notifier.py — Notification Telegram vers le bot de contrôle (§7.2,
§3.6 : "audit manuel intégral des extractions les 3 premières semaines").

Portée volontairement réduite par rapport au `control_bot` complet du CDC
(§4.4) : ce module sait seulement ENVOYER une notification, il ne reçoit ni
n'exécute aucune commande (/pause, /dashboard, etc. — hors périmètre P1,
dépendent de modules qui n'existent pas encore). Voir docs/DECISIONS.md.

Utilise l'API HTTP simple du bot Telegram (`sendMessage`) via `urllib`
(bibliothèque standard) plutôt que Telethon ou une dépendance tierce comme
`requests` : un bot Telegram n'a pas besoin d'une session utilisateur, un
jeton suffit, et un POST JSON basique ne justifie pas une dépendance
supplémentaire.

Déterministe (§4.4 : control_bot ✅) — aucune décision, un simple relais.
Ne lève jamais d'exception vers l'appelant : un échec d'envoi ne doit
jamais interrompre l'ingestion (fail-safe, invariant #9 — un message
d'audit manqué est regrettable, un pipeline d'ingestion arrêté est pire).
"""

import json
import urllib.error
import urllib.request

TELEGRAM_API_BASE = "https://api.telegram.org"


def send_notification(bot_token: str, chat_id: str, message: str, timeout: float = 10.0) -> bool:
    """Envoie `message` au chat_id configuré via le bot de contrôle.
    Retourne True si l'API Telegram a confirmé l'envoi, False sinon
    (jamais d'exception : voir docstring du module)."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return bool(body.get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return False


def send_document(bot_token: str, chat_id: str, file_path: str, filename: str, caption: str = "", timeout: float = 30.0) -> bool:
    """Envoie un fichier (ex: dashboard HTML, §4.6) en pièce jointe
    Telegram (`sendDocument`, pas `sendMessage`) — utilise `requests`
    (déjà une dépendance déclarée du projet, capital_client.py/
    market_data.py) plutôt que `urllib` : construire un corps
    multipart/form-data à la main serait disproportionné pour ce seul
    besoin, contrairement à `sendMessage` (JSON simple, urllib suffit).
    Jamais d'exception vers l'appelant, même contrat que send_notification."""
    import requests

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (filename, f, "text/html")},
                timeout=timeout,
            )
        return resp.ok and bool(resp.json().get("ok"))
    except (requests.RequestException, OSError, ValueError):
        return False


def format_signal_notification(asset, direction, entry_price, stop_price, take_profits, extraction_status) -> str:
    if extraction_status != "ok":
        return (
            f"⚠️ Signal INCOMPLET (audit requis)\n"
            f"Actif détecté : {asset or 'non résolu'}\n"
            f"Direction : {direction or 'inconnue'}\n"
            f"→ extraction_status={extraction_status}"
        )
    tps = ", ".join(f"TP{i+1}={v}" if v is not None else f"TP{i+1}=ouvert" for i, v in enumerate(take_profits))
    return (
        f"📥 Signal extrait (audit §3.6)\n"
        f"{asset} — {direction}\n"
        f"Entrée : {entry_price} | SL : {stop_price}\n"
        f"{tps}"
    )


def format_matinale_notification(asset, biais_corps, sentiment_tag, contradiction_detectee) -> str:
    if contradiction_detectee:
        return (
            f"🔶 Contradiction Matinale détectée (§3.4)\n"
            f"{asset} : corps={biais_corps} vs tag déclaré={sentiment_tag}\n"
            f"Biais du corps conservé, tag ignoré — aucune décision automatique."
        )
    # Le format réel du canal (20/08/2026, docs/DECISIONS.md) laisse
    # souvent biais_corps="indetermine" (aucune phrase heuristique dans un
    # texte technique de niveaux/FVG/Fibonacci) — afficher le tag déclaré
    # dans ce cas plutôt qu'un "indetermine" peu informatif, sans jamais
    # inventer de biais si aucun des deux n'est résolu.
    biais_effectif = biais_corps if biais_corps != "indetermine" else (sentiment_tag or "indetermine")
    return f"📰 Matinale — {asset} : biais {biais_effectif}"


# ---------------------------------------------------------------------------
# Ouverture / gestion / clôture de position (§7.2) — ajouté le 20/08/2026,
# absent avant (voir docs/DECISIONS.md) : les deux premiers trades réels du
# Flux B avaient été ouverts sans aucune notification.
# ---------------------------------------------------------------------------

def format_trade_opened_notification(actif, source, direction, entry_price, stop_price, size) -> str:
    return (
        f"🟢 Position ouverte — {actif} ({source})\n"
        f"{direction} | entrée : {entry_price} | stop initial : {stop_price} | taille : {size}"
    )


def format_trade_partial_notification(actif, source, palier_label, r_atteint) -> str:
    return (
        f"🎯 Clôture partielle — {actif} ({source})\n"
        f"Palier {palier_label} touché : {r_atteint:+.2f}R"
    )


def format_trade_closed_notification(actif, source, r_multiple_total, raison, pnl_net) -> str:
    return (
        f"🔴 Position clôturée — {actif} ({source})\n"
        f"Raison : {raison} | R-multiple total : {r_multiple_total:+.2f}R | P&L net : {pnl_net:+.2f}€"
    )
