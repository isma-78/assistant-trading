"""
telegram_listener.py — Capture brute des messages du canal Station X,
threading obligatoire (§3.2), routage vers message_classifier puis parser,
persistance (§4.5), notification d'audit manuel (§3.6, §7.2).

Déterministe (§4.4 : ✅). Aucune interprétation du contenu ici : capture,
classification, extraction et écriture, dans cet ordre, sans jugement.
L'extraction (parser.py) est elle-même déterministe — écart documenté par
rapport au §4.4 littéral (qui prévoyait un LLM), voir docs/DECISIONS.md.

Deux couches séparées volontairement :
- process_message() : logique métier pure, testable sans connexion
  Telegram réelle (texte + métadonnées en entrée, écriture DB + notif)
- run_listener()    : câblage Telethon (import différé — cette dépendance
  n'est nécessaire qu'en production, jamais pour les tests)

Premier lancement de run_listener() : authentification Telethon
interactive obligatoire (code Telegram envoyé par SMS/app, puis mot de
passe 2FA du compte personnel d'Ismaël) — ne peut pas être scripté sans sa
présence au clavier au moment de l'exécution. Voir docs/DECISIONS.md.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from src.audit_notifier import (
    format_matinale_notification,
    format_signal_notification,
    send_notification,
)
from src.db import connection_scope, init_db
from src.message_classifier import MessageCategory, classify
from src.parser import extract_matinale, extract_signal, extract_suivi

logger = logging.getLogger(__name__)


def process_message(
    db_path: str,
    channel: str,
    telegram_msg_id: int,
    reply_to_msg_id: Optional[int],
    raw_text: str,
    received_at: Optional[str] = None,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    audit_all: bool = True,
) -> MessageCategory:
    """Traite un message déjà capturé : classe, extrait, journalise,
    notifie pour audit manuel (§3.6). Idempotent : un message déjà vu
    (même channel + même telegram_msg_id, contrainte UNIQUE de
    raw_messages) est ignoré silencieusement — un redémarrage du listener
    ne duplique jamais l'historique."""
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    category = classify(raw_text)

    with connection_scope(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM raw_messages WHERE channel = ? AND telegram_msg_id = ?",
            (channel, telegram_msg_id),
        ).fetchone()
        if existing is not None:
            logger.info(
                "Message déjà capturé (channel=%s, msg_id=%s), ignoré", channel, telegram_msg_id
            )
            return category

        cursor = conn.execute(
            "INSERT INTO raw_messages "
            "(telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type, processed) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, category.value),
        )
        raw_message_id = cursor.lastrowid

        if category == MessageCategory.SIGNAL:
            _handle_signal(
                conn, raw_message_id, channel, reply_to_msg_id, raw_text, received_at,
                bot_token, chat_id, audit_all,
            )
        elif category == MessageCategory.MATINALE:
            _handle_matinale(conn, raw_message_id, raw_text, received_at, bot_token, chat_id, audit_all)
        elif category == MessageCategory.SUIVI:
            _handle_suivi(conn, raw_message_id, reply_to_msg_id, raw_text, received_at)
        # AUTRE : raw_messages suffit, aucun traitement supplémentaire (§3.10)

    return category


def _handle_signal(
    conn, raw_message_id, channel, reply_to_msg_id, raw_text, received_at, bot_token, chat_id, audit_all
):
    extraction = extract_signal(raw_text, reply_to_msg_id=reply_to_msg_id)
    # confiance déterministe (invariant #10) : 1.0 si tous les champs requis
    # sont résolus, 0.0 sinon — pas un score LLM auto-déclaré (§3.6 du CDC),
    # voir docs/DECISIONS.md.
    confiance = 1.0 if extraction.extraction_status == "ok" else 0.0
    statut = "a_valider" if extraction.extraction_status == "ok" else "rejete"
    raison_rejet = None if extraction.extraction_status == "ok" else "extraction_incomplete"

    conn.execute(
        "INSERT INTO signals "
        "(raw_message_id, source, type, actif, sens, entree_min, entree_max, stop_loss, "
        " tp1, tp2, tp3, confiance, statut, raison_rejet, created_at) "
        "VALUES (?, ?, 'signal', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            raw_message_id, channel, extraction.asset, extraction.direction,
            # entree_min = entree_max = prix unique : le canal ne publie pas
            # de zone pour le signal final (correction empirique du §3.3,
            # voir docs/DECISIONS.md).
            extraction.entry_price, extraction.entry_price, extraction.stop_price,
            extraction.take_profits[0], extraction.take_profits[1], extraction.take_profits[2],
            confiance, statut, raison_rejet, received_at,
        ),
    )

    if bot_token and chat_id and audit_all:
        message = format_signal_notification(
            extraction.asset, extraction.direction, extraction.entry_price,
            extraction.stop_price, extraction.take_profits, extraction.extraction_status,
        )
        send_notification(bot_token, chat_id, message)


def _handle_matinale(conn, raw_message_id, raw_text, received_at, bot_token, chat_id, audit_all):
    extraction = extract_matinale(raw_text)
    for summary in extraction.assets:
        conn.execute(
            "INSERT INTO matinale_summaries "
            "(raw_message_id, raw_asset_mention, actif, biais_corps, sentiment_tag, "
            " contradiction_detectee, published_at, prix_courant, zone_depart_min, zone_depart_max, "
            " niveau_majeur, fvg_haut, fvg_bas, fib_50, fib_618, fib_786) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                raw_message_id, summary.raw_asset_mention, summary.asset,
                summary.biais_corps, summary.sentiment_tag,
                int(summary.contradiction_detectee), received_at,
                summary.prix_courant, summary.zone_depart_min, summary.zone_depart_max,
                summary.niveau_majeur, summary.fvg_haut, summary.fvg_bas,
                summary.fib_50, summary.fib_618, summary.fib_786,
            ),
        )
        # Une contradiction (§3.4) est toujours notifiée, même après la
        # fenêtre d'audit intégral des 3 premières semaines (§7.2 : liste
        # de notifications permanentes, indépendante de audit_all).
        if bot_token and chat_id and (audit_all or summary.contradiction_detectee):
            message = format_matinale_notification(
                summary.asset or summary.raw_asset_mention, summary.biais_corps,
                summary.sentiment_tag, summary.contradiction_detectee,
            )
            send_notification(bot_token, chat_id, message)


def _handle_suivi(conn, raw_message_id, reply_to_msg_id, raw_text, received_at):
    extraction = extract_suivi(raw_text, reply_to_msg_id=reply_to_msg_id)
    conn.execute(
        "INSERT INTO suivi_events (raw_message_id, reply_to_msg_id, event, pips, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (raw_message_id, reply_to_msg_id, extraction.event, extraction.pips, received_at),
    )


async def _backfill_history(
    client, channel_entity, channel_label: str, limit: int, db_path: str, bot_token, chat_id
) -> int:
    """Traite les `limit` derniers messages déjà présents dans le canal
    (plus ancien -> plus récent), via le même client/session déjà
    authentifié — pas de deuxième connexion, pas de risque de conflit
    d'accès concurrent au fichier .session (voir docs/DECISIONS.md).
    `channel_entity` (résolu, éventuellement un id numérique) sert à
    interroger Telethon ; `channel_label` (la valeur brute de config,
    lisible) est celle stockée en base — cohérent avec ce que fait le
    listener en direct. Idempotent comme process_message() : sans effet
    sur un message déjà capturé par ailleurs. audit_all=False : pas de
    notification pour de l'historique, seules les contradictions Matinale
    (§3.4, toujours notifiées) le seraient. Retourne le nombre de messages
    traités."""
    count = 0
    async for message in client.iter_messages(channel_entity, limit=limit, reverse=True):
        count += 1
        try:
            process_message(
                db_path=db_path,
                channel=channel_label,
                telegram_msg_id=message.id,
                reply_to_msg_id=message.reply_to_msg_id,
                raw_text=message.raw_text or "",
                received_at=message.date.isoformat() if message.date else None,
                bot_token=bot_token,
                chat_id=chat_id,
                audit_all=False,
            )
        except Exception:
            logger.exception("Erreur lors du backfill du message %s", message.id)
    return count


def run_listener(
    config, db_path: str, session_path: str = "data/telethon_session", backfill_limit: int = 0
) -> None:
    """Point d'entrée production. Import Telethon différé : cette
    dépendance n'est nécessaire qu'ici, jamais pour process_message() ni
    pour les tests. Bloque (client.run_until_disconnected()) — à lancer
    comme processus long (service systemd ou équivalent), pas depuis un
    script ponctuel.

    Python 3.14 a supprimé la création implicite de boucle asyncio par
    asyncio.get_event_loop() (RuntimeError si aucune boucle n'est
    "running" ni explicitement définie). Telethon 1.36.0 y accède de
    façon synchrone dès la construction de TelegramClient (pas seulement
    à .start()), ce que l'API historique de Telethon suppose toujours
    possible. On crée donc la boucle explicitement avant toute
    utilisation de Telethon plutôt que de dépendre d'un comportement
    implicite que Python ne fournit plus — voir docs/DECISIONS.md."""
    import asyncio

    from telethon import TelegramClient, events

    init_db(db_path)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    client = TelegramClient(
        session_path, int(config.telegram_api_id), config.telegram_api_hash, loop=loop
    )

    # TELEGRAM_CHANNEL peut être un @username (canal public) ou un id
    # numérique (canal privé rejoint par lien d'invitation, sans username
    # résolvable — cas de Station X, voir docs/DECISIONS.md). Telethon
    # n'accepte un id que sous forme d'int Python, jamais de chaîne.
    try:
        channel_entity = int(config.telegram_channel)
    except ValueError:
        channel_entity = config.telegram_channel

    @client.on(events.NewMessage(chats=channel_entity))
    async def _on_message(event):
        try:
            process_message(
                db_path=db_path,
                channel=config.telegram_channel,
                telegram_msg_id=event.message.id,
                reply_to_msg_id=event.message.reply_to_msg_id,
                raw_text=event.message.raw_text or "",
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
            )
        except Exception:
            # Un message malformé ne doit jamais arrêter la capture des
            # suivants : aucune décision d'ordre ne dépend de ce module en
            # P1 (pas d'executor branché), contrairement au risk_engine où
            # le fail-safe (invariant #9) bloque au contraire toute suite.
            logger.exception(
                "Erreur de traitement d'un message Telegram (msg_id=%s) — capture non interrompue",
                event.message.id,
            )

    logger.info(
        "Démarrage de l'écoute sur %s (première connexion : authentification interactive requise)",
        config.telegram_channel,
    )
    client.start(phone=config.telegram_phone)

    if backfill_limit > 0:
        logger.info("Backfill des %d derniers messages de %s...", backfill_limit, config.telegram_channel)
        n = loop.run_until_complete(
            _backfill_history(
                client, channel_entity, config.telegram_channel, backfill_limit, db_path,
                config.telegram_bot_token, config.telegram_chat_id,
            )
        )
        logger.info("Backfill terminé : %d messages traités.", n)

    client.run_until_disconnected()


if __name__ == "__main__":
    import argparse

    from src.config import load_config

    logging.basicConfig(level=logging.INFO)
    parser_cli = argparse.ArgumentParser()
    parser_cli.add_argument(
        "--backfill", type=int, default=0,
        help="Nombre de messages récents à traiter au démarrage (0 = désactivé, comportement normal)",
    )
    args = parser_cli.parse_args()

    app_config = load_config()
    run_listener(app_config, db_path=app_config.db_path, backfill_limit=args.backfill)
