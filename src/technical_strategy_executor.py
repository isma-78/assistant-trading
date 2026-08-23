"""
technical_strategy_executor.py — Moteur générique de boucle autonome
pour toute "stratégie technique complémentaire" (§2.11 du CDC) :
Hypothèse #1 (`trend_executor.py`), #3 et #2 (nouveaux, 21/08/2026,
voir docs/HYPOTHESES.md) en sont trois instances, chacune un simple jeu
de paramètres passés à `run_technical_strategy_loop` ci-dessous.

Extrait de `trend_executor.py` le 21/08/2026 (voir docs/DECISIONS.md) :
avant cette date, la boucle de l'Hypothèse #1 était codée en dur dans ce
fichier (résolution HOUR, source "hypothesis", 8 actifs...) — construire
l'Hypothèse #3 (résolution M15) et #2 (détection différente) en copiant
ce fichier trois fois aurait dupliqué ~150 lignes de logique de boucle
identique (gestion des ordres, coupe-circuits, /stop_urgence, enveloppes)
pour un seul vrai point de variation par hypothèse : quelle fonction de
détection appeler, sur quelle résolution de bougie, avec quels
identifiants/compte, quel jeu d'actifs. Centralisé ici une seule fois ;
`trend_executor.py` (Hypothèse #1), `hypothesis3_executor.py` et
`hypothesis2_executor.py` ne contiennent plus que leurs propres
paramètres — un bug corrigé ici (ex: la gestion /stop_urgence) est
corrigé pour les trois à la fois, jamais à re-corriger trois fois.

Comportement de l'Hypothèse #1 volontairement inchangé par cette
extraction (mêmes appels, mêmes paramètres, testé par régression via
tests/test_trend_executor.py, toujours vert après refactor) — TOUJOURS
vrai après l'ajout de la couche session/multi-timeframe du 23/08/2026
(`session_gated`/`require_regime_confirmation`, voir
docs/DECISIONS.md) : H1 n'appelle jamais `run_technical_strategy_loop`
avec ces deux paramètres, ils restent à leur valeur par défaut (False),
son comportement est donc inchangé par construction, pas seulement par
absence de régression constatée.

Aucun LLM dans la décision d'entrée (invariant #1) — `entry_fn` est
toujours une fonction déterministe pure (trend_strategy.evaluate_entry,
ict_strategy.evaluate_entry...). Le seul LLM de ce module intervient
exactement comme pour Station X, via executor.manage_open_trades ->
trade_analyzer.analyze_closed_trade (narratif post-trade uniquement).
"""

import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

import requests

from src import circuit_breaker_store
from src.capital_client import CapitalApiError, CapitalClient
from src.db import connection_scope
from src.envelope_store import load_or_create_envelope
from src.executor import (
    cancel_stale_working_orders,
    check_pending_fills,
    force_close_all_open_trades,
    manage_open_trades,
    open_signal,
)
from src.go_nogo import GoNoGoStatus
from src.market_data import Candle, get_candles
from src.regime_confirmation import CRYPTO_ASSETS, confirm_regime
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import MA_PERIOD

# Heures UTC d'ouverture de session (Asie/Londres/New York), couche
# session/multi-timeframe du 23/08/2026 (voir docs/DECISIONS.md,
# docs/HYPOTHESES.md) — H2/H3/H4/H5 UNIQUEMENT, jamais H1 (exclue de
# toute cette couche, trend_executor.py n'appelle jamais
# run_technical_strategy_loop avec session_gated=True). Bornes fixes,
# faits calendaires au même titre que les fenêtres macro fixes du §2.9
# — ne comptent pas dans le budget de paramètres §2.11 (décision
# explicite d'Ismaël). Fenêtre = l'heure UTC pleine suivant chaque
# ouverture (ex: 00h00-00h59 pour l'Asie), pas une largeur réglable
# séparée — évite d'introduire un second paramètre non demandé.
SESSION_OPEN_HOURS_UTC = (0, 8, 13)

logger = logging.getLogger(__name__)


def _should_generate_signals(session_gated: bool, hour_utc: int, asset: str) -> bool:
    """Fonction pure, extraite pour être testable indépendamment de la
    boucle infinie de `run_technical_strategy_loop`. `session_gated=
    False` (cas de H1, jamais appelée avec ce paramètre) : toujours
    True, comportement strictement inchangé.

    Exemption crypto (23/08/2026, voir docs/DECISIONS.md) : BTCUSD/ETHUSD
    (`regime_confirmation.CRYPTO_ASSETS`) ne sont jamais gatés par la
    fenêtre de session — le marché crypto ne ferme jamais (contrairement
    au forex/indices/GOLD, fermés hors session), une fenêtre calquée sur
    les heures d'ouverture Asie/Londres/New York n'a pas de sens dessus.
    Vérifiée avant `session_gated` : même pour H1 (jamais gatée de toute
    façon) le résultat est identique, mais l'ordre documente clairement
    que la crypto est un cas à part, pas un sous-cas du gate de session."""
    if asset in CRYPTO_ASSETS:
        return True
    return not session_gated or hour_utc in SESSION_OPEN_HOURS_UTC

# Marge au-delà de MA_PERIOD pour garantir un historique suffisant même
# si quelques bougies manquent côté broker — indépendant de la
# résolution (nombre de bougies, pas une durée).
CANDLE_COUNT = MA_PERIOD + 20

# Codée en dur, jamais dérivée de config.capital_environment — même
# garde-fou structurel qu'executor._DEMO_BASE_URL (invariant #4).
# Dupliquée plutôt qu'importée depuis executor.py : ce module ne doit
# dépendre d'aucun détail interne d'executor.py au-delà de ses
# fonctions publiques.
_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"


def _next_synthetic_msg_id() -> int:
    """Identifiant synthétique pour raw_messages.telegram_msg_id — ce
    flux ne vient pas de Telegram. Précision milliseconde : un appel API
    Capital.com prend déjà plus d'une milliseconde, donc deux évaluations
    successives ne peuvent pas collisionner en pratique avec la
    contrainte UNIQUE(channel, telegram_msg_id) — et deux hypothèses
    tournant en parallèle utilisent chacune leur propre `channel`
    (voir run_technical_strategy_loop), donc aucun risque de collision
    inter-process non plus."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _has_active_signal_or_trade(db_path: str, asset: str, source: str) -> bool:
    """Évite de générer un nouveau signal sur un actif qui a déjà un
    signal en attente ou un trade actif pour CETTE source précise — un
    seul signal/trade actif à la fois par (actif, source)."""
    with connection_scope(db_path) as conn:
        signal_row = conn.execute(
            "SELECT 1 FROM signals WHERE actif = ? AND source = ? AND statut = 'a_valider' LIMIT 1",
            (asset, source),
        ).fetchone()
        if signal_row is not None:
            return True
        trade_row = conn.execute(
            "SELECT 1 FROM trades WHERE actif = ? AND source = ? AND statut IN ('en_attente', 'ouvert') LIMIT 1",
            (asset, source),
        ).fetchone()
        return trade_row is not None


def _default_describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : entrée {signal.direction} "
        f"(entrée={signal.entry_price}, stop={signal.stop_price})"
    )


def _generate_and_queue_signal(
    db_path: str, client: CapitalClient, asset: str, *,
    source: str, resolution: str, entry_fn: Callable[[str, List[Candle]], Optional[object]],
    channel: str, hypothesis_label: str,
    describe_signal: Optional[Callable[[str, str, object], str]] = None,
    require_regime_confirmation: bool = False,
) -> None:
    """Évalue `entry_fn` sur `asset` et, si un signal se déclenche et
    qu'aucun signal/trade de cette source n'est déjà actif dessus,
    l'enregistre en base (statut='a_valider') — jamais directement en
    ordre : passe ensuite par la même porte (validator + risk_engine) que
    Station X, via open_signal(), appelé séparément dans la même
    itération de boucle.

    `describe_signal` : formatte le texte d'audit (raw_messages.raw_text)
    — optionnel, chaque hypothèse peut décrire son propre mécanisme
    (ex: "rupture de canal Donchian(20)" pour l'Hypothèse #1) ; par
    défaut, une description générique neutre.

    `signal.take_profit` (Hypothèse #4 UNIQUEMENT, voir
    mean_reversion_strategy.MeanReversionSignal) est écrit dans la
    colonne dédiée `signals.take_profit` si présente sur l'objet signal
    (`getattr`, jamais un accès direct — TrendSignal/ICT n'ont pas ce
    champ) ; `signal.tp1`/`signal.tp2` (Hypothèse #5 UNIQUEMENT, voir
    hypothesis5_strategy.Hypothesis5Signal — ajout du 23/08/2026, même
    patron `getattr`) sont écrits dans les colonnes `signals.tp1`/`tp2`.
    Un signal ne porte JAMAIS les deux à la fois (`take_profit` et
    `tp1`/`tp2` ciblent des dispatches de gestion de position mutuellement
    exclusifs dans `executor._evaluate_position_management`, voir
    docs/DECISIONS.md, 21/08/2026 puis 23/08/2026) — pour H1/H2/H3
    (TrendSignal/ICT, ni l'un ni l'autre champ), les trois colonnes
    restent NULL, comme avant.

    `require_regime_confirmation` (H3/H4 UNIQUEMENT, couche session/
    multi-timeframe du 23/08/2026, voir docs/DECISIONS.md) : si True,
    le signal produit par `entry_fn` est REJETÉ (jamais persisté) si
    `regime_confirmation.confirm_regime` ne confirme pas — le
    déclencheur propre à l'hypothèse (Donchian, Bollinger...) reste
    inchangé, cette confirmation s'ajoute PAR-DESSUS, elle ne le
    remplace jamais. False par défaut : H1 (jamais appelée avec ce
    paramètre) et H2/H5 (option C, voir docs/HYPOTHESES.md — déjà
    couvertes par leur régime structurel) ne sont jamais affectées.
    Crypto (BTCUSD/ETHUSD) : `confirm_regime` retourne toujours True
    pour ces deux actifs, quelle que soit l'heure (voir
    `regime_confirmation.CRYPTO_ASSETS`) — géré à l'intérieur de
    `confirm_regime`, pas ici, ce module reste agnostique de la liste
    des actifs crypto."""
    if _has_active_signal_or_trade(db_path, asset, source):
        return

    candles = get_candles(client, asset, resolution=resolution, count=CANDLE_COUNT)
    signal = entry_fn(asset, candles)
    if signal is None:
        return

    if require_regime_confirmation:
        confirmed = confirm_regime(client, asset, signal.direction, resolution, datetime.now(timezone.utc))
        if not confirmed:
            logger.info(
                "%s : signal %s sur %s rejeté par la confirmation de régime croisée (indice non aligné)",
                hypothesis_label, signal.direction, asset,
            )
            return

    now = datetime.now(timezone.utc).isoformat()
    describe = describe_signal or _default_describe_signal
    raw_text = describe(hypothesis_label, asset, signal)
    with connection_scope(db_path) as conn:
        raw_cursor = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type, processed) "
            "VALUES (?, NULL, ?, ?, ?, 'signal', 1)",
            (_next_synthetic_msg_id(), channel, now, raw_text),
        )
        raw_message_id = raw_cursor.lastrowid
        conn.execute(
            "INSERT INTO signals (raw_message_id, source, type, actif, sens, entree_min, entree_max, stop_loss, "
            "tp1, tp2, take_profit, confiance, statut, created_at) "
            "VALUES (?, ?, 'signal', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'a_valider', ?)",
            (
                raw_message_id, source, asset, signal.direction,
                signal.entry_price, signal.entry_price, signal.stop_price,
                getattr(signal, "tp1", None), getattr(signal, "tp2", None),
                getattr(signal, "take_profit", None),
                signal.confidence, now,
            ),
        )
    logger.info(
        "%s : signal généré sur %s (%s, entrée=%s, stop=%s)",
        hypothesis_label, asset, signal.direction, signal.entry_price, signal.stop_price,
    )


def run_technical_strategy_loop(
    config, db_path: str, *,
    source: str,
    assets: List[str],
    resolution: str,
    entry_fn: Callable[[str, List[Candle]], Optional[object]],
    api_key: Optional[str],
    identifier: Optional[str],
    password: Optional[str],
    account_id: Optional[str],
    channel: str,
    process_name: str,
    hypothesis_label: str,
    describe_signal: Optional[Callable[[str, str, object], str]] = None,
    interval_seconds: int = 60,
    session_gated: bool = False,
    require_regime_confirmation: bool = False,
) -> None:
    """Boucle continue générique d'une stratégie technique complémentaire
    (§2.11). Un seul point de variation par appelant : `source`,
    `assets`, `resolution`, `entry_fn`, les identifiants/compte broker à
    utiliser, et les libellés (`channel`, `process_name`,
    `hypothesis_label`) qui distinguent cette hypothèse des autres dans
    les tables partagées et les logs.

    Mêmes garde-fous que executor.run_executor_loop (démo verrouillée
    structurellement via _DEMO_BASE_URL, go_nogo non applicable en démo
    §4.1, fail-safe par itération, invariant #7) — voir sa docstring pour
    le raisonnement détaillé, non dupliqué ici.

    `session_gated`/`require_regime_confirmation` (couche session/
    multi-timeframe du 23/08/2026, voir docs/DECISIONS.md) : False par
    défaut — `trend_executor.py` (Hypothèse #1) n'appelle jamais cette
    fonction avec l'un ou l'autre, donc H1 est byte pour byte inchangée
    par cet ajout (aucun test de régression H1 ne peut échouer à cause
    de ces deux paramètres, ils n'existent tout simplement pas dans son
    appel). `session_gated=True` restreint la GÉNÉRATION DE NOUVEAUX
    SIGNAUX aux heures UTC pleines suivant chaque ouverture de session
    (`SESSION_OPEN_HOURS_UTC`) — la gestion des positions déjà ouvertes
    (remplissages, annulations, trailing, coupe-circuits) continue à
    CHAQUE itération, jamais gatée : geler la gestion du risque en
    dehors des heures de session serait dangereux, pas juste
    conservateur. Exemption crypto (23/08/2026, voir docs/DECISIONS.md) :
    BTCUSD/ETHUSD ignorent `session_gated` (analyse continue, voir
    `_should_generate_signals`) et `require_regime_confirmation`
    (pass-through, voir `regime_confirmation.CRYPTO_ASSETS`) — le reste
    des actifs de `assets` n'est pas affecté par cette exemption."""
    import time

    import anthropic

    from src.asset_whitelist import build_asset_whitelist
    from src.config import ConfigError
    from src.market_data import get_eur_conversion_rate

    if not api_key or not identifier or not password:
        raise ConfigError(
            f"{process_name} : identifiants Capital.com manquants pour la source '{source}' "
            "(clé API/identifiant/mot de passe) — voir .env.example"
        )
    # Ciblage explicite de compte, même garde-fou qu'executor.
    # run_executor_loop (incident réel du 20/08/2026, voir
    # docs/DECISIONS.md) — jamais le compte "préféré", instable.
    if not account_id:
        raise ConfigError(
            f"{process_name} : identifiant de compte manquant pour la source '{source}' "
            "— requis pour cibler explicitement le compte (jamais le compte \"préféré\", "
            "instable, voir docs/DECISIONS.md du 20/08/2026)"
        )

    client = CapitalClient(api_key, identifier, password, _DEMO_BASE_URL)
    client.login()
    client.switch_account(account_id)
    anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    caps = RiskCaps(
        risk_percent_default=config.risk_percent_default,
        risk_percent_boosted=config.risk_percent_boosted,
        envelope_initial=config.envelope_initial,
    )
    usd_to_eur = get_eur_conversion_rate(client, "USD")
    jpy_to_eur = get_eur_conversion_rate(client, "JPY")
    full_whitelist = build_asset_whitelist(usd_to_eur, jpy_to_eur)
    whitelist = {k: v for k, v in full_whitelist.items() if k in assets}
    risk_engine = RiskEngine(caps=caps, whitelist=whitelist)
    go_nogo_status = GoNoGoStatus(allowed=True, reason="mode démo — verrou réel non applicable (§4.1 du CDC)")

    envelope_managers, envelope_ids = {}, {}
    for asset in assets:
        envelope_id, manager = load_or_create_envelope(db_path, asset, "demo", caps.envelope_initial, source=source)
        key = (asset, source)
        envelope_ids[key], envelope_managers[key] = envelope_id, manager

    logger.info(
        "Démarrage de la boucle %s (source=%s, résolution=%s, intervalle=%ds, %d actifs)",
        hypothesis_label, source, resolution, interval_seconds, len(assets),
    )

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Surcouche anomalie système (§2.7) — même sonde de
            # connectivité que executor.run_executor_loop.
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

            # /stop_urgence (§7.1) : chaque boucle ferme uniquement ses
            # propres positions (cette source).
            stop_event_id = circuit_breaker_store.get_unhandled_stop_urgence_event_id(db_path, process_name)
            if stop_event_id is not None:
                closed = force_close_all_open_trades(
                    db_path, client, envelope_managers, envelope_ids,
                    include_sources=[source],
                    anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
                )
                circuit_breaker_store.mark_stop_urgence_handled(db_path, process_name, stop_event_id)
                logger.warning("Arrêt d'urgence traité : %d position(s) %s fermée(s)", closed, hypothesis_label)
                time.sleep(interval_seconds)
                continue

            for asset in assets:
                if not _should_generate_signals(session_gated, now.hour, asset):
                    continue
                _generate_and_queue_signal(
                    db_path, client, asset,
                    source=source, resolution=resolution, entry_fn=entry_fn,
                    channel=channel, hypothesis_label=hypothesis_label,
                    describe_signal=describe_signal,
                    require_regime_confirmation=require_regime_confirmation,
                )

            check_pending_fills(
                db_path, client, sources=[source],
                bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
            )
            cancel_stale_working_orders(db_path, client)

            with connection_scope(db_path) as conn:
                pending_signals = conn.execute(
                    "SELECT * FROM signals WHERE statut = 'a_valider' AND source = ?", (source,)
                ).fetchall()
            for signal_row in pending_signals:
                key = (signal_row["actif"], source)
                if key not in envelope_managers:
                    continue  # ne devrait pas arriver (whitelist déjà restreinte à `assets`), audit
                open_signal(
                    db_path, client, signal_row, risk_engine, whitelist,
                    envelope_managers[key], envelope_ids[key],
                    config.confidence_threshold, go_nogo_status,
                    config.telegram_bot_token, config.telegram_chat_id,
                )

            manage_open_trades(
                db_path, client, risk_engine, envelope_managers, envelope_ids,
                include_sources=[source],
                anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
            )
        except Exception:
            logger.exception("Erreur non gérée dans la boucle %s — nouvelle tentative au prochain cycle", hypothesis_label)

        time.sleep(interval_seconds)
