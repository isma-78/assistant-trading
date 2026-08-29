"""
technical_strategy_executor.py — Moteur générique de boucle autonome
pour toute "stratégie technique complémentaire" (§2.11 du CDC) :
Hypothèse #1 (`trend_executor.py`), #2, #3, #4, #5 en sont des instances,
chacune un simple jeu de paramètres passés à `run_technical_strategy_loop`
ci-dessous.

Extrait de `trend_executor.py` le 21/08/2026 (voir docs/DECISIONS.md) :
avant cette date, la boucle de l'Hypothèse #1 était codée en dur dans ce
fichier (résolution HOUR, source "hypothesis", 8 actifs...) — construire
les autres hypothèses en copiant ce fichier plusieurs fois aurait dupliqué
~150 lignes de logique de boucle identique (gestion des ordres,
coupe-circuits, /stop_urgence, enveloppes) pour un seul vrai point de
variation par hypothèse : quelle fonction de détection appeler, sur
quelle résolution de bougie, avec quels identifiants/compte, quel jeu
d'actifs. Centralisé ici une seule fois ; `trend_executor.py` (Hypothèse
#1) et les 4 `hypothesisN_executor.py` ne contiennent plus que leurs
propres paramètres — un bug corrigé ici est corrigé pour toutes à la
fois, jamais à re-corriger plusieurs fois.

Comportement de l'Hypothèse #1 volontairement inchangé par cette
extraction (mêmes appels, mêmes paramètres, testé par régression via
tests/test_trend_executor.py, toujours vert après refactor) — TOUJOURS
vrai après la couche session/multi-timeframe du 23/08/2026 et sa
révision du même jour (`require_regime_confirmation`, voir
docs/DECISIONS.md) : H1 n'appelle jamais `run_technical_strategy_loop`
avec ce paramètre, il reste à sa valeur par défaut (False), son
comportement est donc inchangé par construction, pas seulement par
absence de régression constatée.

**RÉVISION MAJEURE du 23/08/2026, fin de journée** (conception corrigée
d'Ismaël, voir docs/HYPOTHESES.md/docs/DECISIONS.md) — remplace la
première version de la couche session/multi-timeframe du même jour :
- La génération de NOUVEAUX SIGNAUX n'est plus gatée par la fenêtre de
  session pour AUCUN actif, AUCUNE hypothèse. Le paramètre `session_
  gated` et la fonction `_should_generate_signals` (avec l'exemption
  crypto qui allait avec) sont retirés — devenus obsolètes puisque plus
  aucune hypothèse n'a besoin de bloquer la génération. Chaque actif de
  `assets` est évalué à CHAQUE cycle (~60s), toute la journée.
- Pour H3/H4 (`require_regime_confirmation=True`), la confirmation de
  régime croisée n'est plus recalculée à la volée pour chaque signal
  individuel (ancien coût : jusqu'à 8 appels réseau par cycle, un par
  signal généré) — elle est désormais un CONTEXTE mis en cache,
  rafraîchi aux 3 ouvertures de session UTC (`SESSION_OPEN_HOURS_UTC`,
  qui change donc de rôle : d'une porte sur la génération à une cadence
  de rafraîchissement) PLUS une fois au démarrage du process (évite un
  trou de plusieurs heures de confirmation absente après un
  redémarrage/déploiement — écart mineur assumé par rapport à "calculée
  aux 3 ouvertures" au sens strict, documenté dans docs/DECISIONS.md).
  Entre deux rafraîchissements, le contexte en cache reste actif — un
  trigger produit à N'IMPORTE QUELLE heure est comparé à ce contexte,
  jamais recalculé pour lui seul. Avant le tout premier rafraîchissement
  (aucun cache), le contexte est vide -> aucune confirmation possible ->
  tout trigger H3/H4 est rejeté (fail-safe, invariant #7), jamais un
  état indéterminé traité comme confirmé.

Aucun LLM dans la décision d'entrée (invariant #1) — `entry_fn` est
toujours une fonction déterministe pure (trend_strategy.evaluate_entry,
ict_strategy.evaluate_entry...). Le seul LLM de ce module intervient
exactement comme pour Station X, via executor.manage_open_trades ->
trade_analyzer.analyze_closed_trade (narratif post-trade uniquement).
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

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
    reconcile_ghost_positions,
)
from src.go_nogo import GoNoGoStatus
from src.market_data import Candle, get_candles
from src.regime_confirmation import compute_index_regimes, derive_confirmed_regime
from src.retry import retry_with_backoff
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import MA_PERIOD

# Heures UTC d'ouverture de session (Asie/Londres/New York) — H2/H3/H4/H5
# UNIQUEMENT, jamais H1. Depuis la révision du 23/08/2026 (voir docstring
# du module), ce ne sont PLUS des bornes de génération de signaux : elles
# fixent uniquement la cadence de rafraîchissement du contexte de régime
# confirmé (`require_regime_confirmation`, H3/H4 seules). Bornes fixes,
# faits calendaires au même titre que les fenêtres macro fixes du §2.9 —
# ne comptent pas dans le budget de paramètres §2.11 (décision explicite
# d'Ismaël, réaffirmée lors de cette révision).
SESSION_OPEN_HOURS_UTC = (0, 8, 13)

logger = logging.getLogger(__name__)

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

# Échelonnement INTRA-cycle (27/08/2026, voir docs/DECISIONS.md,
# executor.INTER_SIGNAL_PROCESSING_DELAY_SECONDS pour le raisonnement
# complet) — dupliqué plutôt qu'importé, même motif que _DEMO_BASE_URL
# ci-dessus. H5 en particulier passe de ~0 à ~111 signaux/semaine
# atteignant get_price_snapshot() une fois le garde-fou Option B
# débloqué en démo (798 rejets mesurés sur 7 jours avant déploiement,
# toutes hypothèses confondues) — ce délai évite qu'un cycle qui
# débloque plusieurs signaux d'un coup les envoie tous consécutivement.
INTER_SIGNAL_PROCESSING_DELAY_SECONDS = 1.0


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


EXTRA_RESOLUTION_CANDLE_COUNT = 100  # FIGÉ — profondeur pour EMA/Ichimoku/RSI(14) sur une résolution de confirmation


def _generate_and_queue_signal(
    db_path: str, client: CapitalClient, asset: str, *,
    source: str, resolution: str, entry_fn: Callable[[str, List[Candle]], Optional[object]],
    channel: str, hypothesis_label: str,
    describe_signal: Optional[Callable[[str, str, object], str]] = None,
    require_regime_confirmation: bool = False,
    confirmed_regime: Optional[str] = None,
    extra_resolutions: Optional[List[str]] = None,
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
    champ) ; `signal.tp1`/`signal.tp2` (Hypothèses #2/#3/#5, voir
    hypothesis2_strategy.py/hypothesis3_strategy.py/hypothesis5_strategy.py
    — même patron `getattr`) sont écrits dans les colonnes `signals.tp1`/
    `tp2`. Un signal ne porte JAMAIS les deux à la fois (`take_profit` et
    `tp1`/`tp2` ciblent des dispatches de gestion de position mutuellement
    exclusifs dans `executor._evaluate_position_management`, voir
    docs/DECISIONS.md) — pour H1 (TrendSignal nu, ni l'un ni l'autre
    champ), les trois colonnes restent NULL, comme avant.

    `require_regime_confirmation`/`confirmed_regime` (H3/H4 UNIQUEMENT,
    révision du 23/08/2026 de la couche session/multi-timeframe, voir
    docs/DECISIONS.md) : si `require_regime_confirmation` est True, le
    signal produit par `entry_fn` est REJETÉ (jamais persisté) si sa
    direction ne correspond pas à `confirmed_regime` — le contexte de
    régime actuellement en cache, calculé et rafraîchi par l'appelant
    (`run_technical_strategy_loop`, voir sa docstring), jamais recalculé
    ici. Le déclencheur propre à l'hypothèse (Donchian, Bollinger...)
    reste inchangé, cette confirmation s'ajoute PAR-DESSUS, elle ne le
    remplace jamais. `require_regime_confirmation=False` par défaut : H1
    (jamais appelée avec ce paramètre) et H2/H5 (option C, voir
    docs/HYPOTHESES.md — déjà couvertes par leur régime structurel) ne
    sont jamais affectées.

    `extra_resolutions` (H2/L2 UNIQUEMENT, 29/08/2026, voir
    docs/DECISIONS.md point C) : résolutions supplémentaires du MÊME
    actif à récupérer (profondeur réduite, `EXTRA_RESOLUTION_CANDLE_
    COUNT`) et passer à `entry_fn` en arguments positionnels
    supplémentaires (`entry_fn(asset, candles, *candles_extra)`) —
    nécessaire pour la confluence multi-timeframe de L2
    (`hypothesis2_strategy_v2.evaluate_entry(asset, candles_m15,
    candles_h1, candles_h4)`), que le contrat générique à une seule
    résolution ne permettait pas. `None` par défaut : H1/H3/H4/H5
    strictement inchangées (un seul appel `entry_fn(asset, candles)`,
    comme avant)."""
    if _has_active_signal_or_trade(db_path, asset, source):
        return

    candles = get_candles(client, asset, resolution=resolution, count=CANDLE_COUNT)
    if extra_resolutions:
        extra_candles = [
            get_candles(client, asset, resolution=extra_resolution, count=EXTRA_RESOLUTION_CANDLE_COUNT)
            for extra_resolution in extra_resolutions
        ]
        signal = entry_fn(asset, candles, *extra_candles)
    else:
        signal = entry_fn(asset, candles)
    if signal is None:
        return

    if require_regime_confirmation and signal.direction != confirmed_regime:
        logger.info(
            "%s : signal %s sur %s rejeté — contexte de régime actuellement actif : %s",
            hypothesis_label, signal.direction, asset, confirmed_regime,
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


def _should_refresh_regime_context(last_refresh_hour: Optional[int], hour_utc: int) -> bool:
    """Fonction pure, extraite pour être testable indépendamment de la
    boucle infinie. True : au tout premier appel (`last_refresh_hour is
    None` — pas de trou de plusieurs heures après un redémarrage) OU à
    l'entrée dans une nouvelle heure d'ouverture de session
    (`SESSION_OPEN_HOURS_UTC`) différente de la dernière déjà
    rafraîchie — jamais deux fois pour la même heure (évite de marteler
    l'API à chaque cycle de 60s pendant toute l'heure de session)."""
    if last_refresh_hour is None:
        return True
    return hour_utc in SESSION_OPEN_HOURS_UTC and hour_utc != last_refresh_hour


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
    require_regime_confirmation: bool = False,
    startup_offset_seconds: int = 0,
    confirming_resolution: Optional[str] = None,
    extra_resolutions: Optional[List[str]] = None,
    legacy_sources: Optional[List[str]] = None,
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

    `require_regime_confirmation` (H3/H4 UNIQUEMENT, révision du
    23/08/2026 de la couche session/multi-timeframe, voir
    docs/DECISIONS.md) : False par défaut — `trend_executor.py`
    (Hypothèse #1) et H2/H5 n'appellent jamais cette fonction avec ce
    paramètre, donc leur comportement est inchangé par construction.
    Quand True, un CONTEXTE de régime confirmé (`{actif: "long"|"short"|
    None}`) est maintenu en mémoire pour toute la durée du process,
    rafraîchi via `regime_confirmation.compute_index_regimes` +
    `derive_confirmed_regime` (`_should_refresh_regime_context` décide
    quand) — jamais recalculé pour chaque signal individuel. La
    génération de nouveaux signaux (`_generate_and_queue_signal`) tourne
    à CHAQUE itération pour TOUS les actifs de `assets`, plus aucune
    fenêtre de session ne la bloque (retiré le 23/08/2026, voir
    docs/DECISIONS.md — l'ancien paramètre `session_gated` n'existe
    plus). La gestion des positions déjà ouvertes (remplissages,
    annulations, trailing, coupe-circuits) continue elle aussi à CHAQUE
    itération, comme avant cette révision : geler la gestion du risque
    serait dangereux, pas juste conservateur.

    `confirming_resolution` (défaut None, 25/08/2026, voir
    docs/HYPOTHESES.md "cycle 2") : résolution utilisée pour
    `compute_index_regimes` (US30/US100), indépendante de `resolution`
    (bougies propres de l'actif). None -> `resolution` réutilisée pour
    les deux, comportement identique à avant ce paramètre (vérifié par
    régression). Permet un candidat "entrée M15 / confirmation HOUR"
    (H3/H4), rendu possible par le correctif d'alignement par horodatage
    du 25/08/2026 dans `backtest_engine.py`. Sans effet quand
    `require_regime_confirmation=False` (H1, H2, H5).

    `startup_offset_seconds` (défaut 0, 24/08/2026, voir
    docs/DECISIONS.md) : pause fixe unique avant le premier appel réseau
    de ce process (avant même `login()`) — échelonne les 6 process
    (executor_loop, trend_executor, H2-H5) qui, tous démarrés avec
    interval_seconds=60, finissaient par cogner l'API Capital.com au
    même instant à chaque minute depuis la même IP. Chaque appelant
    (`trend_executor.py`, `hypothesisN_executor.py`) passe une valeur
    fixe distincte (~10s d'écart) — pas un mécanisme dynamique, un
    simple décalage constant.

    `legacy_sources` (29/08/2026, voir docs/DECISIONS.md, point 2 —
    déploiement des refontes L1-L5) : sources d'une VERSION PRÉCÉDENTE
    de cette hypothèse (ex. `["hypothesis"]` quand `source=
    "hypothesis_v2"`) dont des positions étaient encore ouvertes au
    moment de la bascule. `_has_active_signal_or_trade`/la génération de
    signaux restent scopées STRICTEMENT à `source` (jamais aux sources
    historiques — un nouveau signal n'est jamais généré sous l'ancienne
    étiquette). Seules la réconciliation des positions fantômes, la
    détection de remplissage et la gestion des positions ouvertes
    (trailing/clôture) sont étendues à `legacy_sources`, avec leurs
    propres enveloppes chargées séparément — sans cela, les positions
    encore ouvertes sous l'ancienne source deviendraient invisibles à
    TOUT process dès que l'ancien process est arrêté (aucun ne
    surveillerait plus jamais leur `source`). `None` par défaut :
    comportement strictement inchangé pour tout appelant existant
    (H2/H4/H5, aucune position ouverte au moment de leur bascule — voir
    docs/DECISIONS.md)."""
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

    time.sleep(startup_offset_seconds)

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

    management_sources = [source] + list(legacy_sources or [])

    envelope_managers, envelope_ids = {}, {}
    for asset in assets:
        for src in management_sources:
            envelope_id, manager = load_or_create_envelope(db_path, asset, "demo", caps.envelope_initial, source=src)
            key = (asset, src)
            envelope_ids[key], envelope_managers[key] = envelope_id, manager

    # Contexte de régime confirmé (H3/H4 uniquement, voir docstring) —
    # vide au démarrage : tant qu'aucun rafraîchissement n'a eu lieu,
    # `regime_context.get(asset)` renvoie None pour tout actif, donc
    # `_generate_and_queue_signal` rejette tout trigger (fail-safe,
    # invariant #7) jusqu'au premier rafraîchissement.
    regime_context: Dict[str, Optional[str]] = {}
    last_regime_refresh_hour: Optional[int] = None

    logger.info(
        "Démarrage de la boucle %s (source=%s, résolution=%s, intervalle=%ds, %d actifs)",
        hypothesis_label, source, resolution, interval_seconds, len(assets),
    )

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Surcouche anomalie système (§2.7) — même sonde de
            # connectivité que executor.run_executor_loop. Nouvelle
            # tentative avec backoff court avant d'abandonner (24/08/2026,
            # voir docs/DECISIONS.md) : un seul 429 transitoire ici
            # sautait TOUT le cycle (aucune entrée, aucune gestion des
            # positions ouvertes), le point de défaillance le plus visible
            # du rate-limiting Capital.com après le déploiement H2-H5.
            try:
                retry_with_backoff(
                    client.get_account_balance,
                    exceptions=(CapitalApiError, requests.exceptions.RequestException),
                )
                circuit_breaker_store.record_api_result(db_path, process_name, True)
            except (CapitalApiError, requests.exceptions.RequestException):
                circuit_breaker_store.record_api_result(
                    db_path, process_name, False, config.telegram_bot_token, config.telegram_chat_id,
                )
                logger.exception("Échec de la sonde de connectivité API — itération sautée")
                time.sleep(interval_seconds)
                continue

            # /stop_urgence (§7.1) : chaque boucle ferme ses propres
            # positions ET celles de ses sources historiques
            # (`legacy_sources`, 29/08/2026) — une commande d'urgence
            # doit fermer TOUT ce que ce process surveille, jamais
            # laisser une position v1 ouverte derrière lui.
            stop_event_id = circuit_breaker_store.get_unhandled_stop_urgence_event_id(db_path, process_name)
            if stop_event_id is not None:
                closed = force_close_all_open_trades(
                    db_path, client, envelope_managers, envelope_ids,
                    include_sources=management_sources,
                    anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
                )
                circuit_breaker_store.mark_stop_urgence_handled(db_path, process_name, stop_event_id)
                logger.warning("Arrêt d'urgence traité : %d position(s) %s fermée(s)", closed, hypothesis_label)
                time.sleep(interval_seconds)
                continue

            if require_regime_confirmation and _should_refresh_regime_context(last_regime_refresh_hour, now.hour):
                index_regimes = compute_index_regimes(client, confirming_resolution or resolution)
                regime_context = {asset: derive_confirmed_regime(asset, index_regimes) for asset in assets}
                last_regime_refresh_hour = now.hour
                logger.info("%s : contexte de régime rafraîchi -> %s", hypothesis_label, regime_context)

            for asset in assets:
                _generate_and_queue_signal(
                    db_path, client, asset,
                    source=source, resolution=resolution, entry_fn=entry_fn,
                    channel=channel, hypothesis_label=hypothesis_label,
                    describe_signal=describe_signal,
                    require_regime_confirmation=require_regime_confirmation,
                    confirmed_regime=regime_context.get(asset),
                    extra_resolutions=extra_resolutions,
                )

            reconcile_ghost_positions(db_path, client, source_filter=lambda s: s in management_sources)
            check_pending_fills(
                db_path, client, sources=management_sources,
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
                    environment=config.capital_environment,
                )
                time.sleep(INTER_SIGNAL_PROCESSING_DELAY_SECONDS)

            manage_open_trades(
                db_path, client, risk_engine, envelope_managers, envelope_ids,
                include_sources=management_sources,
                anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
            )
        except Exception:
            logger.exception("Erreur non gérée dans la boucle %s — nouvelle tentative au prochain cycle", hypothesis_label)

        time.sleep(interval_seconds)
