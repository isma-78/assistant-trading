"""
control_bot.py — Bot de contrôle Telegram (§7.1 du CDC) : /etat, /pause
[actif], /reprendre [actif], /stop_urgence, /dashboard, /aide. Les 9
autres commandes du §7.1 restent hors périmètre (dépendent de modules
encore absents : confidence_scorer, allocator, hypothesis_engine officiel
— voir docs/DECISIONS.md), portée volontairement réduite comme
audit_notifier.py l'a déjà fait pour les notifications.

Liste des commandes centralisée dans `COMMANDS` (nom + description
courte) — source UNIQUE, jamais dupliquée : alimente à la fois /aide et
le menu natif Telegram (`setMyCommands`, ré-enregistré à chaque
démarrage de run_control_bot_loop, voir sa docstring). Toute commande
ajoutée doit être déclarée ici, nulle part ailleurs (20/08/2026, demande
explicite d'Ismaël).

/dashboard (§4.6) génère la page HTML (src/dashboard.py) et l'envoie en
pièce jointe Telegram plutôt que via un lien vers un serveur web, même
temporaire — écart assumé au §4.6 littéral, validé par Ismaël le
20/08/2026, voir docs/DECISIONS.md.

Process autonome séparé (4e session tmux sur le VPS, aux côtés de
telegram_listener, executor_loop, trend_executor) : réutilise le MÊME
bot Telegram que audit_notifier.py (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID),
pas le compte d'écoute Station X (Telethon) — deux mécanismes Telegram
strictement distincts.

Ce module N'OUVRE JAMAIS de session broker et ne touche jamais
CapitalClient (invariant #1 : aucune décision de risque/exécution ici,
même si ce n'est pas un LLM — séparation contrôle/exécution délibérée).
/stop_urgence, /pause et /reprendre écrivent uniquement un événement dans
`circuit_breaker_events` (via circuit_breaker_store.py) ; c'est
executor.run_executor_loop / trend_executor.run_trend_loop, qui
possèdent déjà leur propre CapitalClient authentifié, qui agissent
réellement dessus au cycle suivant (jusqu'à ~60s de latence pour
/stop_urgence — voir leur docstring).

Sécurité (§5) : seul le chat Telegram configuré (TELEGRAM_CHAT_ID, celui
d'Ismaël en conversation privée avec ce bot) peut déclencher une
commande — tout message d'un autre chat_id est journalisé et ignoré,
jamais exécuté. Pas de nouvelle variable d'environnement : dans une
conversation privée Telegram, message.chat.id == message.from.id, donc
TELEGRAM_CHAT_ID (déjà utilisé pour recevoir les notifications) sert
aussi de liste blanche d'expéditeur autorisé.
"""

import json
import logging
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional, Tuple

from src.audit_notifier import TELEGRAM_API_BASE, send_document, send_notification
from src.circuit_breaker_store import clear_breaker, trigger_manual_pause, trigger_stop_urgence
from src.dashboard import generate_dashboard_html
from src.db import connection_scope

logger = logging.getLogger(__name__)

# Source UNIQUE des commandes implémentées (nom, description courte) —
# voir docstring du module. Alimente /aide ET le menu natif Telegram.
# L'ordre reflète celui affiché par /aide et dans le menu Telegram.
COMMANDS = [
    ("etat", "Positions ouvertes, enveloppes, statut coupe-circuit par actif"),
    ("pause", "Stoppe les entrées (global, ou /pause ACTIF pour un seul actif)"),
    ("reprendre", "Réactive les entrées (global, ou /reprendre ACTIF)"),
    ("stop_urgence", "Ferme toutes les positions ouvertes, bloque toutes les entrées"),
    ("dashboard", "Génère et envoie le dashboard complet (§4.6)"),
    ("analyse_causale", "Dernières analyses causales de coupe-circuits (§3.11)"),
    ("aide", "Affiche la liste des commandes disponibles"),
]
KNOWN_COMMANDS = {name for name, _ in COMMANDS}


def parse_command(text: str) -> Optional[Tuple[str, Optional[str]]]:
    """Reconnaît `/commande [ARGUMENT]`. Retourne None si `text` n'est
    pas une commande (pas de préfixe '/') — jamais une exception, les
    messages non-commandes sont simplement ignorés."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    body = text[1:].strip()
    if not body:
        return None
    parts = body.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip().upper() if len(parts) > 1 and parts[1].strip() else None
    return command, arg


def format_etat(db_path: str) -> str:
    """§7.1 /etat : positions ouvertes, enveloppes, statut coupe-circuit
    par actif — lecture seule, aucune décision."""
    with connection_scope(db_path) as conn:
        open_trades = conn.execute(
            "SELECT actif, source, direction, prix_entree_reel, prix_entree_prevu, stop_loss_courant "
            "FROM trades WHERE statut = 'ouvert' ORDER BY actif"
        ).fetchall()
        envelopes = conn.execute(
            "SELECT actif, source, capital_courant FROM envelopes ORDER BY actif, source"
        ).fetchall()
        reserve = conn.execute("SELECT reserve_totale FROM reserve_ledger ORDER BY id DESC LIMIT 1").fetchone()
        global_breakers = conn.execute(
            "SELECT breaker_type FROM circuit_breaker_events WHERE scope = 'global' AND cleared_at IS NULL"
        ).fetchall()
        asset_breakers = conn.execute(
            "SELECT actif, source, breaker_type FROM circuit_breaker_events WHERE scope = 'asset' AND ("
            "  (breaker_type IN ('week_r', 'drawdown_r', 'manual_pause') AND cleared_at IS NULL)"
            "  OR (breaker_type = 'day_r' AND substr(triggered_at, 1, 10) = date('now'))"
            ") ORDER BY actif"
        ).fetchall()

    lines = ["\U0001F4CB État du système"]

    lines.append("\nPositions ouvertes :")
    if not open_trades:
        lines.append("  Aucune")
    for t in open_trades:
        entry = t["prix_entree_reel"] if t["prix_entree_reel"] is not None else t["prix_entree_prevu"]
        lines.append(f"  {t['actif']} ({t['source']}) {t['direction']} — entrée={entry}, stop={t['stop_loss_courant']}")

    lines.append("\nEnveloppes :")
    if not envelopes:
        lines.append("  Aucune")
    for e in envelopes:
        lines.append(f"  {e['actif']} ({e['source']}) : {e['capital_courant']}€")
    lines.append(f"  Réserve globale : {reserve['reserve_totale'] if reserve else 0.0}€")

    lines.append("\nCoupe-circuits actifs :")
    if not global_breakers and not asset_breakers:
        lines.append("  Aucun")
    for b in global_breakers:
        lines.append(f"  GLOBAL : {b['breaker_type']}")
    for b in asset_breakers:
        lines.append(f"  {b['actif']} ({b['source'] or 'toutes sources'}) : {b['breaker_type']}")

    return "\n".join(lines)


CAUSAL_ANALYSIS_DISPLAY_LIMIT = 5

_CATEGORY_LABELS = {
    "anomalie_technique": "⚠️ Anomalie technique",
    "evenement_marche": "\U0001F30D Événement de marché",
    "hypothese_pattern": "\U0001F4CC Hypothèse de pattern (en attente)",
}


def format_analyse_causale(db_path: str) -> str:
    """§3.11 /analyse_causale : dernières entrées de `causal_analysis_log`
    en langage clair — lecture seule, aucune décision. Ajoutée le
    24/08/2026 (voir docs/DECISIONS.md) : jusqu'ici, `causal_analysis_
    log` n'avait AUCUN consommateur réel — le cycle autonome (§3.9) qui
    doit à terme l'exploiter statistiquement n'est pas construit
    (dépend du backtest rétrospectif). Cette commande rend le travail
    déjà fait par `causal_analyzer.py` consultable dès aujourd'hui,
    sans attendre le cycle autonome."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT declencheur, categorie, analyse_texte, action_prise, created_at "
            "FROM causal_analysis_log ORDER BY id DESC LIMIT ?",
            (CAUSAL_ANALYSIS_DISPLAY_LIMIT,),
        ).fetchall()

    lines = ["\U0001F52C Analyse causale — coupe-circuits (§3.11)"]
    if not rows:
        lines.append("\nAucune analyse enregistrée pour l'instant — journalisée automatiquement au prochain coupe-circuit R (day_r/week_r/drawdown_r).")
        return "\n".join(lines)

    for row in rows:
        label = _CATEGORY_LABELS.get(row["categorie"], row["categorie"])
        lines.append(f"\n{label}")
        lines.append(f"  Déclencheur : {row['declencheur']}")
        lines.append(f"  Quand : {row['created_at']}")
        lines.append(f"  {row['analyse_texte']}")
        if row["action_prise"]:
            lines.append(f"  Action prise : {row['action_prise']}")

    return "\n".join(lines)


def format_aide() -> str:
    """§7.1 /aide : liste des commandes RÉELLEMENT implémentées, dérivée
    de COMMANDS — jamais une liste séparée à tenir à jour à la main."""
    lines = ["\U0001F4D6 Commandes disponibles :"]
    for name, description in COMMANDS:
        lines.append(f"/{name} — {description}")
    return "\n".join(lines)


def _send_dashboard(db_path: str, bot_token: Optional[str], chat_id: Optional[str]) -> str:
    """Génère le HTML (src/dashboard.py) et l'envoie en pièce jointe
    Telegram (send_document, pas send_notification — voir docstring du
    module). Retourne un texte de repli si l'envoi échoue ou si
    bot_token/chat_id ne sont pas disponibles (ex: appel direct sans
    passer par _process_update, comme dans certains tests) ; retourne
    une chaîne vide en cas de succès — _process_update n'envoie alors
    aucun message texte supplémentaire, le document sert de réponse."""
    if not bot_token or not chat_id:
        return "Dashboard indisponible : identifiants Telegram manquants."

    html_content = generate_dashboard_html(db_path)
    fd, tmp_path = tempfile.mkstemp(suffix=".html", prefix="dashboard_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_content)
        filename = f"dashboard_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.html"
        sent = send_document(bot_token, chat_id, tmp_path, filename, caption="\U0001F4CA Dashboard Assistant Trading")
    finally:
        os.remove(tmp_path)

    return "" if sent else "Échec de l'envoi du dashboard — réessaie dans un instant."


def handle_command(
    db_path: str, command: str, arg: Optional[str], triggered_by: str = "ismael",
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> str:
    """Dispatch d'une commande déjà authentifiée (voir _process_update).
    Ne touche jamais le broker (voir docstring du module) — écrit
    uniquement l'état de contrôle, lu au cycle suivant par les boucles
    d'exécution. `bot_token`/`chat_id` : uniquement nécessaires pour
    /dashboard (envoi direct d'un fichier, contrairement aux autres
    commandes dont la réponse texte est envoyée par l'appelant)."""
    if command == "etat":
        return format_etat(db_path)

    if command == "pause":
        trigger_manual_pause(db_path, arg, triggered_by)
        cible = arg or "TOUS LES ACTIFS (pause globale)"
        return f"⏸ Pause déclenchée — {cible}. Les entrées sont bloquées jusqu'à /reprendre."

    if command == "reprendre":
        cleared = clear_breaker(db_path, arg, triggered_by)
        cible = arg or "global"
        return f"▶ Reprise — {cleared} événement(s) effacé(s) pour {cible}."

    if command == "stop_urgence":
        trigger_stop_urgence(db_path, triggered_by)
        return (
            "\U0001F6D1 ARRÊT D'URGENCE déclenché — GLOBAL, Station X ET Flux B. "
            "Toutes les positions ouvertes (les deux flux) seront fermées au prochain "
            "cycle de chaque boucle (jusqu'à ~60s), toutes les nouvelles entrées sont "
            "bloquées (Station X ET Flux B, pas seulement celui qui était ouvert).\n\n"
            "⚠️ Tape /reprendre pour tout relancer — sans ça, plus aucune entrée nulle part."
        )

    if command == "dashboard":
        return _send_dashboard(db_path, bot_token, chat_id)

    if command == "analyse_causale":
        return format_analyse_causale(db_path)

    if command == "aide":
        return format_aide()

    return f"Commande inconnue : /{command}\n\n{format_aide()}"


def register_bot_commands(bot_token: str, timeout: float = 10.0) -> bool:
    """Enregistre le menu natif Telegram (`setMyCommands`) — le menu qui
    apparaît quand on tape "/" dans la conversation avec le bot, généré
    depuis COMMANDS (même source que /aide, jamais dupliquée). Appelée à
    chaque démarrage de run_control_bot_loop : toute commande ajoutée à
    COMMANDS puis suivie d'un redémarrage du process se reflète
    automatiquement dans le menu, sans étape manuelle à se rappeler (voir
    docs/DECISIONS.md). Retourne False sans lever d'exception en cas
    d'échec réseau — un menu non rafraîchi n'est jamais bloquant."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/setMyCommands"
    payload = json.dumps(
        {"commands": [{"command": name, "description": description} for name, description in COMMANDS]}
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return bool(body.get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return False


def _get_updates(bot_token: str, offset: Optional[int], timeout: int = 25) -> list:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates?{query}")
    with urllib.request.urlopen(request, timeout=timeout + 10) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates a échoué : {body}")
    return body.get("result", [])


def _process_update(db_path: str, update: dict, authorized_chat_id: str, bot_token: str) -> None:
    message = update.get("message")
    if not message or "text" not in message:
        return
    chat_id = str(message.get("chat", {}).get("id", ""))
    if chat_id != str(authorized_chat_id):
        logger.warning("Commande ignorée : expéditeur non autorisé (chat_id=%s)", chat_id)
        return

    parsed = parse_command(message["text"])
    if parsed is None:
        return
    command, arg = parsed
    logger.info("Commande reçue : /%s %s", command, arg or "")
    reply = handle_command(db_path, command, arg, bot_token=bot_token, chat_id=authorized_chat_id)
    if reply:  # /dashboard réussi retourne "" : le document envoyé sert déjà de réponse
        send_notification(bot_token, authorized_chat_id, reply)


def run_control_bot_loop(config, db_path: str) -> None:
    """Boucle de long-polling `getUpdates` (§7.1). Fail-safe (invariant
    #7) : une erreur réseau ou de traitement interrompt seulement le
    cycle courant, jamais le process — un bot de contrôle qui s'arrête
    silencieusement serait pire qu'un cycle raté."""
    import time

    offset = None
    logger.info("Démarrage du bot de contrôle (chat_id autorisé=%s)", config.telegram_chat_id)
    if register_bot_commands(config.telegram_bot_token):
        logger.info("Menu de commandes Telegram enregistré (%d commandes)", len(COMMANDS))
    else:
        logger.warning("Échec de l'enregistrement du menu de commandes Telegram (setMyCommands) — non bloquant")

    while True:
        try:
            updates = _get_updates(config.telegram_bot_token, offset)
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    _process_update(db_path, update, config.telegram_chat_id, config.telegram_bot_token)
                except Exception:
                    logger.exception("Erreur en traitant la mise à jour %s — passage à la suivante", update.get("update_id"))
        except Exception:
            logger.exception("Erreur non gérée dans la boucle du bot de contrôle — nouvelle tentative")
            time.sleep(5)


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_control_bot_loop(app_config, db_path=app_config.db_path)
