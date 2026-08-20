"""
trend_executor.py — Boucle autonome du Flux B (Hypothèse #1,
docs/HYPOTHESES.md, validée par Ismaël le 20/08/2026). Génère des
signaux via trend_strategy.evaluate_entry sur les 5 actifs sans (ou peu
de) couverture Station X, les fait passer par le MÊME validator.py /
risk_engine.py / executor.py que le flux Station X, dans une enveloppe
démo strictement séparée (source="hypothesis").

Process indépendant de executor.py, tourne dans sa propre session tmux
sur le VPS, aux côtés de telegram_listener et executor_loop (demande
explicite d'Ismaël — pas une fusion dans la boucle Station X). Toutes
les requêtes sur des tables partagées (signaux en attente, trades
ouverts) sont filtrées par source pour ne jamais toucher aux données de
l'autre boucle — voir executor.manage_open_trades/check_pending_fills et
docs/DECISIONS.md pour la discussion des risques de concurrence entre
les deux process sur la même base SQLite.

Aucun LLM dans la décision d'entrée (invariant #1) — trend_strategy est
purement déterministe. Le seul LLM de ce module intervient exactement
comme pour Station X, via executor.manage_open_trades ->
trade_analyzer.analyze_closed_trade (narratif post-trade uniquement).

N=20 (Donchian) et MA(200) sont FIGÉS (docs/HYPOTHESES.md) — jamais
modifiés ici sans une nouvelle entrée datée dans ce fichier et un
redéploiement.
"""

import logging
from datetime import datetime, timezone

import requests

from src import circuit_breaker_store
from src.capital_client import CapitalApiError, CapitalClient
from src.db import connection_scope
from src.envelope_store import load_or_create_envelope
from src.executor import (
    HYPOTHESIS_SOURCE,
    cancel_stale_working_orders,
    check_pending_fills,
    force_close_all_open_trades,
    manage_open_trades,
    open_signal,
)
from src.go_nogo import GoNoGoStatus
from src.market_data import get_candles
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import MA_PERIOD, evaluate_entry

logger = logging.getLogger(__name__)

# Les 5 actifs de la liste blanche sans (ou peu de) signal Station X —
# constat du 19-20/08/2026, figé ici pour rester cohérent avec
# l'Hypothèse #1 telle que validée (docs/HYPOTHESES.md). Toute évolution
# de cette liste = nouvelle entrée datée dans ce fichier de référence,
# pas un ajustement silencieux de cette constante.
HYPOTHESIS_ASSETS = ["US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD"]

# Marge au-delà de MA_PERIOD pour garantir un historique suffisant même
# si quelques bougies manquent côté broker.
CANDLE_COUNT = MA_PERIOD + 20

# Codée en dur, jamais dérivée de config.capital_environment — même
# garde-fou structurel qu'executor._DEMO_BASE_URL (invariant #4). Dupliquée
# plutôt qu'importée depuis executor.py : ce module ne doit dépendre
# d'aucun détail interne d'executor.py au-delà de ses fonctions publiques.
_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"


def _next_synthetic_msg_id() -> int:
    """Identifiant synthétique pour raw_messages.telegram_msg_id — ce
    flux ne vient pas de Telegram. Précision milliseconde : un appel API
    Capital.com prend déjà plus d'une milliseconde, donc deux
    évaluations successives (5 actifs par cycle) ne peuvent pas
    collisionner en pratique avec la contrainte UNIQUE(channel,
    telegram_msg_id)."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _has_active_hypothesis_signal_or_trade(db_path: str, asset: str) -> bool:
    """Évite de générer un nouveau signal sur un actif qui a déjà un
    signal en attente de traitement ou un trade actif du Flux B — un
    seul signal/trade actif à la fois par actif pour cette hypothèse."""
    with connection_scope(db_path) as conn:
        signal_row = conn.execute(
            "SELECT 1 FROM signals WHERE actif = ? AND source = ? AND statut = 'a_valider' LIMIT 1",
            (asset, HYPOTHESIS_SOURCE),
        ).fetchone()
        if signal_row is not None:
            return True
        trade_row = conn.execute(
            "SELECT 1 FROM trades WHERE actif = ? AND source = ? AND statut IN ('en_attente', 'ouvert') LIMIT 1",
            (asset, HYPOTHESIS_SOURCE),
        ).fetchone()
        return trade_row is not None


def _generate_and_queue_signal(db_path: str, client: CapitalClient, asset: str) -> None:
    """Évalue trend_strategy sur `asset` et, si un signal se déclenche et
    qu'aucun signal/trade du Flux B n'est déjà actif dessus, l'enregistre
    en base (statut='a_valider') — jamais directement en ordre : passe
    ensuite par la même porte (validator + risk_engine) que Station X,
    via open_signal(), appelé séparément dans la même itération de
    boucle."""
    if _has_active_hypothesis_signal_or_trade(db_path, asset):
        return

    candles = get_candles(client, asset, resolution="HOUR", count=CANDLE_COUNT)
    signal = evaluate_entry(asset, candles)
    if signal is None:
        return

    now = datetime.now(timezone.utc).isoformat()
    raw_text = (
        f"Flux B — {asset} : rupture de canal Donchian(20) en régime {signal.direction} "
        f"(filtre MA200), entrée={signal.entry_price}, stop={signal.stop_price} "
        f"(Hypothèse #1, docs/HYPOTHESES.md)"
    )
    with connection_scope(db_path) as conn:
        raw_cursor = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type, processed) "
            "VALUES (?, NULL, 'trend_strategy', ?, ?, 'signal', 1)",
            (_next_synthetic_msg_id(), now, raw_text),
        )
        raw_message_id = raw_cursor.lastrowid
        conn.execute(
            "INSERT INTO signals (raw_message_id, source, type, actif, sens, entree_min, entree_max, stop_loss, "
            "confiance, statut, created_at) "
            "VALUES (?, ?, 'signal', ?, ?, ?, ?, ?, ?, 'a_valider', ?)",
            (
                raw_message_id, HYPOTHESIS_SOURCE, asset, signal.direction,
                signal.entry_price, signal.entry_price, signal.stop_price,
                signal.confidence, now,
            ),
        )
    logger.info(
        "Flux B : signal généré sur %s (%s, entrée=%s, stop=%s)",
        asset, signal.direction, signal.entry_price, signal.stop_price,
    )


def run_trend_loop(config, db_path: str, interval_seconds: int = 60) -> None:
    """Boucle continue du Flux B. Intervalle par défaut plus long que
    Station X (60s vs 30s) : les bougies horaires ne changent pas plus
    vite qu'une fois par heure, inutile de solliciter l'API aussi souvent
    que pour la détection de remplissage d'ordres limite.

    Mêmes garde-fous que executor.run_executor_loop (démo verrouillée
    structurellement via _DEMO_BASE_URL, go_nogo non applicable en démo
    §4.1, fail-safe par itération, invariant #7) — voir sa docstring pour
    le raisonnement détaillé, non dupliqué ici."""
    import time

    import anthropic

    from src.asset_whitelist import build_asset_whitelist
    from src.market_data import get_eur_conversion_rate

    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, _DEMO_BASE_URL)
    client.login()
    anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    caps = RiskCaps(
        risk_percent_default=config.risk_percent_default,
        risk_percent_boosted=config.risk_percent_boosted,
        envelope_initial=config.envelope_initial,
    )
    usd_to_eur = get_eur_conversion_rate(client, "USD")
    jpy_to_eur = get_eur_conversion_rate(client, "JPY")
    full_whitelist = build_asset_whitelist(usd_to_eur, jpy_to_eur)
    # Restreinte aux 5 actifs du Flux B — défense en profondeur : un
    # signal hors liste serait de toute façon rejeté par validator.py,
    # mais ce module ne doit pas prétendre couvrir les 8 actifs.
    whitelist = {k: v for k, v in full_whitelist.items() if k in HYPOTHESIS_ASSETS}
    risk_engine = RiskEngine(caps=caps, whitelist=whitelist)
    go_nogo_status = GoNoGoStatus(allowed=True, reason="mode démo — verrou réel non applicable (§4.1 du CDC)")

    envelope_managers, envelope_ids = {}, {}
    for asset in HYPOTHESIS_ASSETS:
        envelope_id, manager = load_or_create_envelope(
            db_path, asset, "demo", caps.envelope_initial, source=HYPOTHESIS_SOURCE,
        )
        key = (asset, HYPOTHESIS_SOURCE)
        envelope_ids[key], envelope_managers[key] = envelope_id, manager

    logger.info(
        "Démarrage de la boucle Flux B (Hypothèse #1, intervalle=%ds, %d actifs)",
        interval_seconds, len(HYPOTHESIS_ASSETS),
    )
    process_name = "trend_executor"

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Surcouche anomalie système (§2.7) — même sonde de
            # connectivité que executor.run_executor_loop (widen du except
            # pour couvrir les ConnectionError bruts inclus, voir sa
            # docstring pour le raisonnement, non dupliqué ici).
            try:
                client.get_account_balance()
                circuit_breaker_store.record_api_result(db_path, process_name, True)
            except (CapitalApiError, requests.exceptions.RequestException):
                circuit_breaker_store.record_api_result(
                    db_path, process_name, False, config.telegram_bot_token, config.telegram_chat_id,
                )
                logger.exception("Échec de la sonde de connectivité API — itération sautée")
                time.sleep(interval_seconds)
                continue

            # /stop_urgence (§7.1) : même mécanisme "une seule fermeture
            # forcée par activation" qu'executor.run_executor_loop, mais
            # suivi séparément par process_name — chaque boucle ferme
            # uniquement ses propres positions (source=hypothesis ici).
            stop_event_id = circuit_breaker_store.get_unhandled_stop_urgence_event_id(db_path, process_name)
            if stop_event_id is not None:
                closed = force_close_all_open_trades(
                    db_path, client, envelope_managers, envelope_ids,
                    include_sources=[HYPOTHESIS_SOURCE],
                    anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
                )
                circuit_breaker_store.mark_stop_urgence_handled(db_path, process_name, stop_event_id)
                logger.warning("Arrêt d'urgence traité : %d position(s) Flux B fermée(s)", closed)
                time.sleep(interval_seconds)
                continue

            for asset in HYPOTHESIS_ASSETS:
                _generate_and_queue_signal(db_path, client, asset)

            check_pending_fills(db_path, client, sources=[HYPOTHESIS_SOURCE])
            cancel_stale_working_orders(db_path, client)

            with connection_scope(db_path) as conn:
                pending_signals = conn.execute(
                    "SELECT * FROM signals WHERE statut = 'a_valider' AND source = ?", (HYPOTHESIS_SOURCE,)
                ).fetchall()
            for signal_row in pending_signals:
                key = (signal_row["actif"], HYPOTHESIS_SOURCE)
                if key not in envelope_managers:
                    continue  # ne devrait pas arriver (whitelist déjà restreinte aux 5 actifs), audit
                open_signal(
                    db_path, client, signal_row, risk_engine, whitelist,
                    envelope_managers[key], envelope_ids[key],
                    config.confidence_threshold, go_nogo_status,
                    config.telegram_bot_token, config.telegram_chat_id,
                )

            manage_open_trades(
                db_path, client, risk_engine, envelope_managers, envelope_ids,
                include_sources=[HYPOTHESIS_SOURCE],
                anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
            )
        except Exception:
            logger.exception("Erreur non gérée dans la boucle Flux B — nouvelle tentative au prochain cycle")

        time.sleep(interval_seconds)


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_trend_loop(app_config, db_path=app_config.db_path)
