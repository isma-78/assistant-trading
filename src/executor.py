"""
executor.py — Exécution démo autonome des signaux Station X ET du Flux B
(palier P2, P2.5). Ouvre, gère et clôture des positions sur le compte
DÉMO Capital.com. Aucun euro réel en jeu (§4.8 : le réel reste hors
périmètre avant Porte A/B, verrouillé par go_nogo.py — ce module n'a même
pas accès à un client configuré sur l'environnement "live").

Deux logiques de sortie sur profit coexistent, distinguées par
`state.tp1 is None` (jamais ambigu : aucun signal Station X n'omet tp1,
voir parser.py) :
- Station X : TP1(50%)/TP2(30%)/TP3(20% sous trailing 2×ATR, §2.10),
  stop au breakeven dès TP1.
- Flux B (Hypothèse #1, aucun TP — voir docs/HYPOTHESES.md) : trailing
  Donchian(20) dès l'ouverture, la même fenêtre qui a fixé le stop
  initial (entrée du 20/08/2026 dans docs/HYPOTHESES.md).

Autonomie complète en démo (demande explicite d'Ismaël pour ce palier) :
aucune validation manuelle par trade. go_nogo.py et risk_engine.py
restent les seuls verrous — écart documenté par rapport au §4.8 littéral
du CDC (qui prévoit une phase de "rodage" avec validation manuelle en
P2), voir docs/DECISIONS.md.

Deux couches, comme telegram_listener.py :
- Fonctions de décision/calcul pures (decide_entry, evaluate_position_
  management, compute_tp_allocations, compute_trailing_stop_level) :
  100% de couverture (demande explicite d'Ismaël, même règle que
  risk_engine.py).
- Orchestration I/O (open_signal, manage_open_trades, run_executor_loop) :
  câblage DB + capital_client, testée avec des doubles mais pas soumise
  à la même exigence de couverture littérale — cohérent avec le
  traitement déjà appliqué à telegram_listener.run_listener().

Invariants appliqués strictement :
- #1 : aucun LLM à aucun niveau de ce module.
- #3 : aucun signal ne devient un ordre sans passer par validator.py PUIS
  risk_engine.evaluate_new_entry — les deux, jamais l'un sans l'autre.
- #5 : tout ordre est journalisé avant envoi (statut "en_cours") et après
  confirmation (deal_id, niveau réel).
- #7 : fail-safe — une erreur interne dans la boucle d'ouverture arrête
  les NOUVELLES entrées ; une erreur dans la boucle de gestion des
  positions déjà ouvertes ne fait RIEN cette itération plutôt que de
  fermer/modifier sur une base incertaine (nuance documentée dans
  docs/DECISIONS.md : abandonner la surveillance d'une position ouverte
  serait plus dangereux que de patienter jusqu'à l'itération suivante).
- Ordres limite uniquement (§2.8) : capital_client.place_limit_order(),
  jamais open_position() (réservée aux scripts de calibration ponctuels).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, List, Optional, Tuple

import requests

from src import circuit_breaker_store, confidence_scorer
from src.audit_notifier import (
    format_trade_closed_notification,
    format_trade_opened_notification,
    format_trade_partial_notification,
    send_notification,
)
from src.capital_client import CapitalApiError, CapitalClient
from src.capital_manager import CapitalManager, apply_trade_result
from src.circuit_breaker import evaluate_exposure_cap
from src.db import connection_scope
from src.envelope_store import load_or_create_envelope, load_reserve_total, persist_trade_result
from src.go_nogo import GoNoGoStatus
from src.market_data import Candle, compute_atr, get_candles, get_price_snapshot
from src.retry import retry_with_backoff
from src.session_marker import compute_market_session
from src.risk_engine import (
    ExistingPosition,
    RiskCaps,
    RiskDecision,
    RiskEngine,
    TradeSignal,
    compute_r_multiple,
    compute_weighted_r_multiple,
)
from src.trade_analyzer import analyze_closed_trade
from src.trade_features_store import record_align_matinale_for_trade
from src.trend_strategy import DONCHIAN_PERIOD, compute_trailing_stop_channel
from src.validator import ValidationResult, validate_signal

logger = logging.getLogger(__name__)

# §2.8 : fenêtre de péremption. Un ordre limite non exécuté au-delà de ce
# délai est annulé (le signal n'est plus d'actualité) — valeur choisie en
# cohérence avec la latence structurelle documentée (10-60s) multipliée
# par une marge large pour laisser le prix atteindre une zone d'entrée
# réaliste sans pour autant laisser un ordre traîner indéfiniment. Voir
# docs/DECISIONS.md (le CDC ne fixe pas de chiffre).
LIMIT_ORDER_EXPIRY_SECONDS = 15 * 60

DIRECTION_TO_API = {"long": "BUY", "short": "SELL"}

# Regroupe toute source non reconnue comme une hypothèse sous l'étiquette
# "stationx" pour le routage des enveloppes (§2.11 : "Métriques calculées
# séparément par source"). signals.source/trades.source stockent la
# valeur brute du canal Telegram (id numérique, voir CLAUDE.md) pour
# Station X, jamais la chaîne littérale "stationx" — cette fonction
# normalise pour le seul besoin du routage d'enveloppe, sans réécrire la
# donnée d'origine. Voir docs/DECISIONS.md (Flux B, palier P2.5).
#
# Bug réel trouvé le 21/08/2026 (voir docs/DECISIONS.md) en préparant H3/H2 :
# cette fonction ne reconnaissait QUE "hypothesis" (H1) — toute autre
# source (y compris une future hypothèse légitime) retombait
# silencieusement sur "stationx", mélangeant ses statistiques avec celles
# de Station X. Corrigé en généralisant à un ENSEMBLE de sources
# hypothèse connues plutôt qu'une seule valeur — toute nouvelle hypothèse
# DOIT être ajoutée à _KNOWN_HYPOTHESIS_SOURCES ici, sinon ses trades
# seront mélangés à ceux de Station X sans erreur ni avertissement.
HYPOTHESIS_SOURCE = "hypothesis"    # Hypothèse #1 (docs/HYPOTHESES.md, 20/08/2026)
HYPOTHESIS3_SOURCE = "hypothesis3"  # Hypothèse #3 (docs/HYPOTHESES.md, 21/08/2026)
HYPOTHESIS2_SOURCE = "hypothesis2"  # Hypothèse #2 (docs/HYPOTHESES.md, 21/08/2026)
HYPOTHESIS4_SOURCE = "hypothesis4"  # Hypothèse #4 (docs/HYPOTHESES.md, 21/08/2026 — validée en démo, non déployée)
HYPOTHESIS5_SOURCE = "hypothesis5"  # Hypothèse #5 (docs/HYPOTHESES.md, 23/08/2026 — sortie §2.10 sur l'entrée ICT de H2, non déployée)
# Backtest rétrospectif (24/08/2026, voir docs/HYPOTHESES.md) : sources
# TOUJOURS distinctes des sources live ci-dessus — jamais mélangées dans
# le même calcul de métriques (backtest_engine.py, scripts/run_
# retrospective_backtest.py).
HYPOTHESIS_BACKTEST_SOURCE = "hypothesis_backtest"
HYPOTHESIS2_BACKTEST_SOURCE = "hypothesis2_backtest"
HYPOTHESIS3_BACKTEST_SOURCE = "hypothesis3_backtest"
HYPOTHESIS4_BACKTEST_SOURCE = "hypothesis4_backtest"
HYPOTHESIS5_BACKTEST_SOURCE = "hypothesis5_backtest"
_KNOWN_HYPOTHESIS_SOURCES = {
    HYPOTHESIS_SOURCE, HYPOTHESIS3_SOURCE, HYPOTHESIS2_SOURCE, HYPOTHESIS4_SOURCE, HYPOTHESIS5_SOURCE,
    HYPOTHESIS_BACKTEST_SOURCE, HYPOTHESIS2_BACKTEST_SOURCE, HYPOTHESIS3_BACKTEST_SOURCE,
    HYPOTHESIS4_BACKTEST_SOURCE, HYPOTHESIS5_BACKTEST_SOURCE,
}

# Correspondance source live -> source backtest, pour le garde-fou Option
# B (voir open_signal ci-dessous et docs/HYPOTHESES.md, 24/08/2026) —
# jamais Station X (hors périmètre de cette demande).
_BACKTEST_SOURCE_BY_LIVE_SOURCE = {
    HYPOTHESIS_SOURCE: HYPOTHESIS_BACKTEST_SOURCE,
    HYPOTHESIS2_SOURCE: HYPOTHESIS2_BACKTEST_SOURCE,
    HYPOTHESIS3_SOURCE: HYPOTHESIS3_BACKTEST_SOURCE,
    HYPOTHESIS4_SOURCE: HYPOTHESIS4_BACKTEST_SOURCE,
    HYPOTHESIS5_SOURCE: HYPOTHESIS5_BACKTEST_SOURCE,
}

# Résolution de bougie utilisée pour recalculer le canal de Donchian du
# trailing (§2.11) de chaque hypothèse — Station X n'y figure jamais
# (state.tp1 is None ne s'applique qu'aux trades sans TP, voir
# _evaluate_position_management). Ajoutée le 21/08/2026 : avant cette
# date, manage_open_trades récupérait TOUJOURS des bougies horaires pour
# ce calcul, ce qui aurait silencieusement appliqué un canal de Donchian
# horaire au trailing de l'Hypothèse #3 (résolution M15) — jamais observé
# en production (H3 non encore déployée à cette date), corrigé avant tout
# déploiement. Toute nouvelle hypothèse ajoutée à _KNOWN_HYPOTHESIS_SOURCES
# doit aussi apparaître ici (repli sur "HOUR" sinon, jamais une exception).
_TREND_CANDLE_RESOLUTION = {
    HYPOTHESIS_SOURCE: "HOUR",
    HYPOTHESIS3_SOURCE: "MINUTE_15",
    HYPOTHESIS2_SOURCE: "MINUTE_15",  # bascule 23/08/2026, couche session/multi-timeframe (voir docs/DECISIONS.md) :
                                       # l'entrée passe de HOUR à M15, la résolution du trailing ATR post-TP2 (§2.10)
                                       # est alignée en conséquence pour ne pas mélanger deux échelles de temps
                                       # différentes entre décision d'entrée et gestion de la même position.
    HYPOTHESIS4_SOURCE: "MINUTE_15",  # bascule 23/08/2026 — sans effet pratique : l'Hypothèse #4 n'a pas de
                                       # trailing (voir take_profit ci-dessous), alignée pour la cohérence seule.
    HYPOTHESIS5_SOURCE: "MINUTE_15",  # bascule 23/08/2026, même raisonnement que H2 — résolution des bougies
                                       # utilisées pour l'ATR du trailing TP3 (§2.10, state.tp1 is not None pour ce
                                       # flux) — H5 n'entre JAMAIS dans la branche Donchian ci-dessous.
}

# Régime de fond utilisé par chaque source hypothèse au moment de
# l'OUVERTURE du trade (ajout 23/08/2026, voir docs/DECISIONS.md —
# bascule du régime de l'Hypothèse #2, MA200 -> structure BOS/CHoCH).
# Mapping figé au moment du déploiement de cette bascule : depuis cette
# date, TOUTE nouvelle ouverture pour une source donnée utilise TOUJOURS
# la même valeur (le code de détection lui-même a changé, pas un
# paramètre runtime) — seuls les trades H2 déjà en base AVANT cette date
# nécessitent un rétro-remplissage séparé (voir db._backfill_regime_type,
# ils ne passent jamais par ce mapping). None = source sans notion de
# régime de fond (Station X) — jamais une valeur devinée.
_REGIME_TYPE_BY_SOURCE = {
    HYPOTHESIS_SOURCE: "ma200",
    HYPOTHESIS3_SOURCE: "ma200",
    HYPOTHESIS2_SOURCE: "structural_bos_choch",
    HYPOTHESIS4_SOURCE: "ma200",
    HYPOTHESIS5_SOURCE: "structural_bos_choch",
}

# Mécanisme de sortie utilisé par chaque source au moment de l'OUVERTURE
# du trade (ajout 23/08/2026, voir docs/DECISIONS.md — sortie à prise de
# profit des Hypothèses #2/#3, décision explicite d'Ismaël, PROSPECTIVE
# UNIQUEMENT). Dimension INDÉPENDANTE de `_REGIME_TYPE_BY_SOURCE`
# ci-dessus (l'une porte sur l'entrée, l'autre sur la sortie — jamais
# fusionnées). Mapping figé au moment de ce déploiement, comme pour
# `_REGIME_TYPE_BY_SOURCE` — les trades H2/H3 déjà en base avant cette
# date nécessitent un rétro-remplissage séparé (voir
# db._backfill_exit_type, ils ne passent jamais par ce mapping).
# `.get(source, "tp_partiel")` : toute source absente de ce dict
# (Station X — canal Telegram brut, jamais une des constantes ci-dessus)
# utilise le mécanisme §2.10 TP1/TP2/trailing, celui pour lequel ce
# mécanisme a été construit à l'origine — jamais une valeur devinée pour
# les sources hypothèse elles-mêmes, toutes explicitement listées.
_EXIT_TYPE_BY_SOURCE = {
    HYPOTHESIS_SOURCE: "trailing_pur",   # H1 : seul témoin restant en trailing pur, jamais changé
    HYPOTHESIS3_SOURCE: "tp_partiel",    # bascule du 23/08/2026
    HYPOTHESIS2_SOURCE: "tp_partiel",    # bascule du 23/08/2026
    HYPOTHESIS4_SOURCE: "tp_fixe",       # cible unique, aucun trailing — mécanisme distinct du §2.10
    HYPOTHESIS5_SOURCE: "tp_partiel",    # tp_partiel depuis son origine, inchangé
}

# Couche session/multi-timeframe utilisée par chaque source au moment de
# l'OUVERTURE du trade (ajout 23/08/2026, voir docs/DECISIONS.md,
# docs/HYPOTHESES.md) — décision explicite d'Ismaël, maintenue après
# mise en garde sur la perte de comparaison isolée H1/H3 : fenêtre de
# session (0h/8h/13h UTC) + exécution M15 pour H2/H3/H4/H5, PLUS
# confirmation de régime croisée (indices US30/US100) pour H3/H4
# uniquement (H2/H5 déjà couvertes par leur régime structurel BOS/CHoCH,
# option C). Dimension INDÉPENDANTE de `_REGIME_TYPE_BY_SOURCE` et
# `_EXIT_TYPE_BY_SOURCE` (porte sur QUAND/COMMENT le signal est généré,
# pas sur son régime d'entrée ni son mécanisme de sortie — jamais
# fusionnées). `.get(source)` : None pour H1 et Station X (aucune notion
# de couche timing pour elles, jamais concernées) — les trades H2/H3/
# H4/H5 déjà en base avant ce déploiement nécessitent un rétro-
# remplissage séparé (voir db._backfill_timing_layer), ils ne passent
# jamais par ce mapping.
_TIMING_LAYER_BY_SOURCE = {
    HYPOTHESIS2_SOURCE: "session_multi_tf",
    HYPOTHESIS3_SOURCE: "session_multi_tf",
    HYPOTHESIS4_SOURCE: "session_multi_tf",
    HYPOTHESIS5_SOURCE: "session_multi_tf",
}


def _envelope_source_key(source: str) -> str:
    return source if source in _KNOWN_HYPOTHESIS_SOURCES else "stationx"


def _is_stationx_source(source: str, telegram_channel: str) -> bool:
    """Vrai uniquement pour Station X. Utilisée par run_executor_loop
    pour filtrer les trades/signaux qu'il gère — jamais une liste figée
    de sources hypothèse à exclure une par une.

    Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : run_executor_
    loop excluait `exclude_sources=[HYPOTHESIS_SOURCE]` — littéralement
    "hypothesis" (H1) seule. Au déploiement de l'Hypothèse #3, ni
    "hypothesis3" ni une future "hypothesis2" n'étaient exclues :
    executor_loop gérait EN DOUBLE, avec la mauvaise enveloppe (celle de
    Station X), les trades de hypothesis3_executor — confirmé en
    production par des échecs 404 répétés des deux process sur les mêmes
    positions, et des positions réelles orphelines côté broker.

    Une première version de ce correctif se basait sur
    `_envelope_source_key(source) == "stationx"` — mais cette fonction
    retombe elle-même sur "stationx" pour toute source NON reconnue
    (conçue pour le routage d'enveloppe, où c'était le bon choix à
    l'origine) : une hypothèse future jamais enregistrée dans
    `_KNOWN_HYPOTHESIS_SOURCES` aurait donc été incluse par erreur dans
    Station X, exactement le mode d'échec qu'on cherche à éliminer.
    Corrigé en comparant `source` à `config.telegram_channel` — la
    valeur EXACTE que `telegram_listener.run_listener` écrit dans
    `signals.source`/`trades.source` pour Station X (voir son appel à
    `process_message(..., channel=config.telegram_channel, ...)`).
    Reconnaissance positive et explicite, jamais "tout ce qui n'est pas
    une hypothèse connue" — toute source qui n'est ni ce canal ni la
    valeur conventionnelle "stationx" (utilisée par les tests, jamais
    écrite en production) est exclue par défaut, fail-safe (invariant
    #7), jamais l'inverse."""
    return source == telegram_channel or source == "stationx"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Décision d'entrée (validator -> risk_engine, jamais l'un sans l'autre)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EntryDecision:
    approved: bool
    validation: ValidationResult
    risk_decision: Optional[RiskDecision] = None
    detail: str = ""


def decide_entry(
    asset: str,
    direction: str,
    entry_price: float,
    stop_price: float,
    confidence: float,
    current_price: Optional[float],
    market_status: str,
    risk_engine: RiskEngine,
    whitelist: dict,
    envelope_balance: float,
    confidence_threshold: float,
    go_nogo_ok: bool,
    existing_position: Optional[ExistingPosition] = None,
    is_weekend: bool = False,
    boosted: bool = False,
) -> EntryDecision:
    """Point d'entrée unique de décision (invariant #3) : validator.py
    PUIS risk_engine.evaluate_new_entry, jamais l'un sans l'autre. Ne
    lève jamais d'exception à elle seule : validate_signal et
    evaluate_new_entry sont déjà fail-safe individuellement."""
    validation = validate_signal(asset, entry_price, stop_price, current_price, market_status, whitelist)
    if not validation.approved:
        return EntryDecision(approved=False, validation=validation, detail=f"Rejeté par validator : {validation.detail}")

    signal = TradeSignal(
        asset=asset, direction=direction, entry_price=entry_price, stop_price=stop_price,
        confidence=confidence, boosted=boosted,
    )
    risk_decision = risk_engine.evaluate_new_entry(
        signal, envelope_balance, confidence_threshold, go_nogo_ok, existing_position, is_weekend,
    )
    if not risk_decision.approved:
        return EntryDecision(
            approved=False, validation=validation, risk_decision=risk_decision,
            detail=f"Rejeté par risk_engine : {risk_decision.reason}",
        )

    return EntryDecision(approved=True, validation=validation, risk_decision=risk_decision, detail="Entrée approuvée")


def compute_tp_allocations(total_units: float, min_units: float) -> Tuple[float, float, float]:
    """Répartit la taille totale en TP1(50%)/TP2(30%)/TP3(20%) (§2.10),
    chaque part arrondie au multiple de min_units. Le reliquat
    d'arrondi est entièrement absorbé par TP3 (le palier "laissé
    courir") pour que la somme des trois parts égale toujours
    exactement total_units — jamais d'unités perdues ni dupliquées."""
    def _round_to_min(x: float) -> float:
        steps = round(x / min_units)
        return round(steps * min_units, 10)

    tp1 = _round_to_min(total_units * 0.5)
    tp2 = _round_to_min(total_units * 0.3)
    tp3 = round(total_units - tp1 - tp2, 10)
    if tp3 < 0:
        # Sur-allocation par arrondi (cas limite, tailles minimales
        # grossières) : TP2 absorbe le dépassement plutôt que de
        # produire une taille négative.
        tp2 = round(tp2 + tp3, 10)
        tp3 = 0.0
    return tp1, tp2, tp3


# ---------------------------------------------------------------------------
# Gestion d'une position ouverte (SL, TP1/TP2/TP3, trailing ATR)
# ---------------------------------------------------------------------------

class ManagementActionType(str, Enum):
    NONE = "none"
    CLOSE_FULL_STOP = "close_full_stop"
    CLOSE_PARTIAL_TP1 = "close_partial_tp1"
    CLOSE_PARTIAL_TP2 = "close_partial_tp2"
    UPDATE_TRAILING_STOP = "update_trailing_stop"
    CLOSE_FULL_TP = "close_full_tp"  # Hypothèse #4 (retour à la moyenne) — voir docs/HYPOTHESES.md, docs/DECISIONS.md


@dataclass(frozen=True)
class OpenTradeState:
    trade_id: int
    deal_id: str
    asset: str
    source: str                # signals/trades.source d'origine — détermine l'enveloppe à créditer/débiter
    direction: str  # "long" | "short"
    entry_price: float
    initial_stop_price: float  # risque initial, jamais modifié (base du R-multiple, §2.1)
    stop_price: float          # stop courant (resserré au fil du temps)
    tp1: Optional[float]
    tp2: Optional[float]
    tp1_hit: bool
    tp2_hit: bool
    remaining_fraction: float  # fraction de la taille initiale encore ouverte
    guaranteed_stop: bool = False  # voir capital_client.update_position_stop, docs/DECISIONS.md (20/08/2026)
    take_profit: Optional[float] = None  # Hypothèse #4 UNIQUEMENT (signals.take_profit, jamais tp1/tp2 —
                                          # voir docs/DECISIONS.md 21/08/2026 : stocker la cible fixe de H4
                                          # dans tp1 aurait à tort déclenché le dispatch Station X 3-paliers)


@dataclass(frozen=True)
class ManagementAction:
    action: ManagementActionType
    fraction_to_close: float = 0.0
    new_stop_price: Optional[float] = None
    exit_price: Optional[float] = None
    r_multiple: Optional[float] = None
    detail: str = ""


def _is_stop_hit(direction: str, current_price: float, stop_price: float) -> bool:
    if direction == "long":
        return current_price <= stop_price
    if direction == "short":
        return current_price >= stop_price
    raise ValueError(f"direction inconnue : {direction!r}")


def _is_target_hit(direction: str, current_price: float, target_price: float) -> bool:
    if direction == "long":
        return current_price >= target_price
    if direction == "short":
        return current_price <= target_price
    raise ValueError(f"direction inconnue : {direction!r}")


def compute_trailing_stop_level(direction: str, current_price: float, atr: float, breakeven: float) -> float:
    """Trailing = 2×ATR(14) (§2.10), plancher explicite au breakeven —
    ne recule jamais en dessous du prix d'entrée (long) / au-dessus
    (short), même si le trailing brut le suggérait."""
    trailing_distance = 2 * atr
    if direction == "long":
        return max(current_price - trailing_distance, breakeven)
    if direction == "short":
        return min(current_price + trailing_distance, breakeven)
    raise ValueError(f"direction inconnue : {direction!r}")


def evaluate_position_management(
    state: OpenTradeState, current_price: float, atr: Optional[float], risk_engine: RiskEngine,
    candles: Optional[List[Candle]] = None,
) -> ManagementAction:
    """Point d'entrée unique de gestion d'une position ouverte. Ne lève
    jamais d'exception : toute erreur interne devient NONE (aucune
    fermeture, aucune mise à jour) plutôt que d'agir sur une base
    incertaine — nuance du fail-safe (invariant #7) documentée dans
    docs/DECISIONS.md : ici, "ne rien faire" protège la position, ce
    n'est pas un renoncement silencieux à un ordre.

    `candles` : uniquement nécessaire pour le trailing Donchian du Flux B
    (state.tp1 is None, aucun signal Station X n'omet tp1) — None pour
    tout trade Station X, sans effet sur son comportement."""
    try:
        return _evaluate_position_management(state, current_price, atr, risk_engine, candles)
    except Exception:
        logger.exception("Erreur interne dans evaluate_position_management (trade_id=%s)", state.trade_id)
        return ManagementAction(action=ManagementActionType.NONE, detail="Erreur interne, aucune action cette itération")


def _evaluate_position_management(
    state: OpenTradeState, current_price: float, atr: Optional[float], risk_engine: RiskEngine,
    candles: Optional[List[Candle]] = None,
) -> ManagementAction:
    if _is_stop_hit(state.direction, current_price, state.stop_price):
        r = compute_r_multiple(state.direction, state.entry_price, state.initial_stop_price, state.stop_price)
        return ManagementAction(
            action=ManagementActionType.CLOSE_FULL_STOP,
            fraction_to_close=state.remaining_fraction,
            exit_price=state.stop_price,
            r_multiple=r,
            detail="Stop touché (initial, breakeven ou trailing)",
        )

    # Hypothèse #4 (retour à la moyenne, §2.11, docs/HYPOTHESES.md,
    # VALIDÉE en démo le 21/08/2026) — 3e patron de sortie, distinct des
    # deux ci-dessous : take-profit FIXE unique (`state.take_profit`,
    # jamais `state.tp1`/`state.tp2` — voir docs/DECISIONS.md pour la
    # raison de cette séparation), clôture à 100% en une fois, AUCUN
    # trailing (conforme invariant #5). Doit être évalué et retourner
    # AVANT le bloc Station X (tp1/tp2) et le bloc Flux B (trailing
    # Donchian ci-dessous) : un trade H4 a toujours state.tp1 is None,
    # donc tomberait à tort dans le trailing Donchian perpétuel du Flux B
    # si cette branche ne renvoyait pas explicitement NONE en l'absence
    # de TP touché.
    if state.take_profit is not None:
        if _is_target_hit(state.direction, current_price, state.take_profit):
            r = compute_r_multiple(state.direction, state.entry_price, state.initial_stop_price, state.take_profit)
            return ManagementAction(
                action=ManagementActionType.CLOSE_FULL_TP,
                fraction_to_close=state.remaining_fraction,
                exit_price=state.take_profit,
                r_multiple=r,
                detail="Take-profit fixe touché (Hypothèse #4) : clôture 100%, aucun trailing",
            )
        return ManagementAction(
            action=ManagementActionType.NONE,
            detail="Take-profit fixe non atteint, stop fixe non touché (Hypothèse #4, pas de trailing)",
        )

    if not state.tp1_hit and state.tp1 is not None and _is_target_hit(state.direction, current_price, state.tp1):
        r = compute_r_multiple(state.direction, state.entry_price, state.initial_stop_price, state.tp1)
        return ManagementAction(
            action=ManagementActionType.CLOSE_PARTIAL_TP1,
            fraction_to_close=0.5,
            exit_price=state.tp1,
            r_multiple=r,
            new_stop_price=state.entry_price,  # §2.10 : SL au breakeven dès TP1 touché
            detail="TP1 touché : clôture 50%, stop déplacé au breakeven",
        )

    if state.tp1_hit and not state.tp2_hit and state.tp2 is not None and _is_target_hit(state.direction, current_price, state.tp2):
        r = compute_r_multiple(state.direction, state.entry_price, state.initial_stop_price, state.tp2)
        return ManagementAction(
            action=ManagementActionType.CLOSE_PARTIAL_TP2,
            fraction_to_close=0.3,
            exit_price=state.tp2,
            r_multiple=r,
            detail="TP2 touché : clôture 30%",
        )

    if state.tp1_hit and state.tp2_hit and atr is not None:
        candidate_stop = compute_trailing_stop_level(state.direction, current_price, atr, state.entry_price)
        stop_decision = risk_engine.evaluate_stop_update(state.stop_price, candidate_stop, state.direction)
        if stop_decision.approved and candidate_stop != state.stop_price:
            return ManagementAction(
                action=ManagementActionType.UPDATE_TRAILING_STOP,
                new_stop_price=candidate_stop,
                detail=f"Trailing ATR (2x) mis à jour : {state.stop_price} -> {candidate_stop}",
            )

    # Flux B (Hypothèse #1) : aucun signal de ce flux n'a jamais de TP
    # (trend_strategy.evaluate_entry n'en calcule pas, voir
    # docs/HYPOTHESES.md, entrée du 20/08/2026) — tp1_hit ne peut donc
    # jamais devenir vrai et le bloc ATR ci-dessus ne s'active jamais pour
    # ces trades. Trailing Donchian(20) dès l'ouverture, pas seulement
    # après un TP1/TP2 qui n'existera jamais. `state.tp1 is None`
    # distingue sans ambiguïté un trade Flux B (aucun signal Station X
    # n'omet tp1, voir parser.py).
    if state.tp1 is None and candles is not None:
        candidate_stop = compute_trailing_stop_channel(state.direction, candles, state.stop_price)
        stop_decision = risk_engine.evaluate_stop_update(state.stop_price, candidate_stop, state.direction)
        if stop_decision.approved and candidate_stop != state.stop_price:
            return ManagementAction(
                action=ManagementActionType.UPDATE_TRAILING_STOP,
                new_stop_price=candidate_stop,
                detail=f"Trailing Donchian(20) mis à jour : {state.stop_price} -> {candidate_stop}",
            )

    return ManagementAction(action=ManagementActionType.NONE, detail="Aucune condition remplie")


# ---------------------------------------------------------------------------
# Orchestration I/O — ouverture d'un signal validé
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuaranteedStopAdjustment:
    stop_price: float           # stop EFFECTIF à utiliser (élargi ou identique au signal d'origine)
    stop_distance: float        # distance correspondante ; 0.0 = pas de stop garanti requis pour cet instrument
    guaranteed_required: bool
    widened: bool                # True si stop_price diffère du stop d'origine du signal


# Marge de sécurité relative appliquée à la distance minimale de stop
# garanti (voir docs/DECISIONS.md, 21/08/2026). Trouvé en conditions
# réelles : `minGuaranteedStopDistance` est réévalué par le broker EN
# DIRECT au moment où l'ordre est réellement traité, pas seulement lu une
# fois par notre appel à get_market_snapshot() — la valeur peut avoir
# dérivé entre-temps (constaté : GOLD passé de 1% à 2% le même jour,
# vraisemblablement lié à la volatilité). Un stop calculé exactement à la
# limite lue par nous est donc régulièrement rejeté (error.invalid.
# stoploss.minvalue), quelques dixièmes de point sous le seuil réel —
# pas un artefact de précision flottante binaire (déjà traité ailleurs,
# voir market_data._mid_of, round(...,8)), une dérive réelle du seuil
# lui-même. +1% de marge relative absorbe cet écart sans fausser le
# sizing de façon significative (invariant #2 : la taille est toujours
# recalculée par risk_engine sur le stop_price réellement retourné ici,
# jamais sur une estimation séparée).
GUARANTEED_STOP_SAFETY_MARGIN = 1.01


def _compute_guaranteed_stop_adjustment(
    client: CapitalClient, epic: str, direction: str, reference_price: float, stop_price: float,
) -> GuaranteedStopAdjustment:
    """Ce compte démo exige un stop garanti sur certains instruments —
    observé sur BTCUSD/ETHUSD dès le palier P0, confirmé aussi sur
    EURUSD lors de la validation des ordres limite au palier P2 (pas
    seulement les cryptos, voir docs/DECISIONS.md).

    Décision du 20/08/2026 (assumée par Ismaël, remplace le rejet du
    16/08/2026 — voir docs/DECISIONS.md pour le raisonnement complet) :
    si le stop budgété par risk_engine est plus serré que le minimum
    imposé par le broker, ÉLARGIT le stop jusqu'à ce minimum plutôt que
    de rejeter l'entrée. L'appelant DOIT redimensionner la position via
    risk_engine.evaluate_new_entry() avec ce nouveau stop_price — cette
    fonction ne fait que déterminer le stop, jamais la taille (invariant
    #2 : elle ne calcule aucun risque en euros elle-même).

    `reference_price` : le prix contre lequel la distance minimale est
    mesurée ET depuis lequel le stop élargi est ancré — le prix d'entrée
    du signal à l'ouverture (`open_signal`), le prix de marché COURANT
    pour un plafonnement de trailing (`_apply_management_action`, voir
    docs/DECISIONS.md 21/08/2026) : le broker applique cette contrainte
    au marché courant, pas seulement au prix d'entrée d'origine d'un
    trade déjà ouvert depuis un moment."""
    market = client.get_market_snapshot(epic)
    dealing_rules = market.get("dealingRules", {})
    min_gs = dealing_rules.get("minGuaranteedStopDistance", {})
    if not min_gs.get("value"):
        return GuaranteedStopAdjustment(stop_price=stop_price, stop_distance=0.0, guaranteed_required=False, widened=False)

    if min_gs.get("unit") == "PERCENTAGE":
        raw_min_distance = reference_price * (min_gs["value"] / 100.0)
    else:
        raw_min_distance = min_gs["value"]
    min_distance = round(raw_min_distance * GUARANTEED_STOP_SAFETY_MARGIN, 8)

    current_distance = abs(reference_price - stop_price)
    if current_distance >= min_distance:
        return GuaranteedStopAdjustment(stop_price=stop_price, stop_distance=current_distance, guaranteed_required=True, widened=False)

    if direction == "long":
        widened_stop_price = round(reference_price - min_distance, 8)
    elif direction == "short":
        widened_stop_price = round(reference_price + min_distance, 8)
    else:
        raise ValueError(f"direction inconnue : {direction!r}")

    return GuaranteedStopAdjustment(stop_price=widened_stop_price, stop_distance=min_distance, guaranteed_required=True, widened=True)


def _check_backtest_confidence_gate(db_path: str, asset: str, source: str, risk_percent: float) -> Optional[str]:
    """Garde-fou Option B (24/08/2026, voir docs/HYPOTHESES.md) : rejette
    un signal AVANT tout calcul de sizing si le backtest rétrospectif du
    couple (actif, hypothèse) est ÉLIGIBLE (seuils backtest, plus stricts
    que le live — `confidence_scorer.PHASE_A_MIN_TRADES_BACKTEST`/
    `PHASE_B_MIN_TRADES_BACKTEST`) ET que son espérance nette est ≤ 0.
    Ne s'applique JAMAIS à Station X (source absente de `_BACKTEST_
    SOURCE_BY_LIVE_SOURCE`). Retourne le détail du rejet, ou None si le
    signal doit continuer son chemin normal (source non concernée,
    couple backtest pas encore éligible, ou espérance positive) — dans
    TOUS ces cas "None", ce garde-fou est un pur no-op, comportement live
    strictement inchangé (vrai par construction tant qu'aucun backtest
    n'a été exécuté pour ce couple : `eligible` reste alors False).

    N'augmente jamais le risque (ne fait que refuser, jamais moduler à
    la hausse) — `risk_engine.py` n'est pas modifié par ce garde-fou.

    Corrigé pendant les tests (24/08/2026, voir docs/DECISIONS.md) :
    n'utilise PAS `ConfidenceScore.eligible` comme critère "assez de
    données" — `evaluate_confidence` (§2.4) exige une espérance nette
    STRICTEMENT POSITIVE parmi ses conditions éliminatoires (c'est un
    score de PROMOTION vers le réel), donc `eligible` est TOUJOURS False
    dès que l'espérance est ≤ 0 : utiliser `eligible` ici aurait rendu ce
    garde-fou définitivement inatteignable, exactement le cas qu'il doit
    détecter. La suffisance d'échantillon est vérifiée séparément via
    `check_min_trades` (mêmes seuils backtest), découplée du signe de
    l'espérance."""
    backtest_source = _BACKTEST_SOURCE_BY_LIVE_SOURCE.get(source)
    if backtest_source is None:
        return None
    score = confidence_scorer.compute_confidence_score(
        db_path, asset, backtest_source, risk_percent,
        phase_a_min_trades=confidence_scorer.PHASE_A_MIN_TRADES_BACKTEST,
        phase_b_min_trades=confidence_scorer.PHASE_B_MIN_TRADES_BACKTEST,
    )
    enough_data, _, phase = confidence_scorer.check_min_trades(
        score.nb_trades, confidence_scorer.PHASE_A_MIN_TRADES_BACKTEST, confidence_scorer.PHASE_B_MIN_TRADES_BACKTEST,
    )
    if not enough_data or score.esperance_r is None or score.esperance_r > 0:
        return None
    return (
        f"Backtest rétrospectif ({backtest_source}) : {score.nb_trades} trades (phase {phase}) "
        f"avec une espérance nette ≤ 0 ({score.esperance_r:.4f}R) — "
        "signal rejeté avant sizing (garde-fou Option B, docs/HYPOTHESES.md 24/08/2026)"
    )


def open_signal(
    db_path: str, client: CapitalClient, signal_row, risk_engine: RiskEngine, whitelist: dict,
    envelope_manager: CapitalManager, envelope_id: int, confidence_threshold: float, go_nogo_status: GoNoGoStatus,
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> Optional[str]:
    """Traite un signal statut='a_valider' : valide, dimensionne, place
    un ordre LIMITE (§2.8), journalise. Retourne le deal_id si un ordre a
    été placé, None sinon (rejet quelque niveau que ce soit — toujours
    journalisé dans risk_decisions, jamais silencieux).

    Deux garde-fous supplémentaires appliqués ICI, autour de
    decide_entry (validator + risk_engine, invariant #3, jamais modifiés
    pour cette intégration) plutôt que dedans — coupe-circuits §2.7 et
    circuit_breaker_store.py sont un module séparé, voir docs/DECISIONS.md :
    1. Blocage global/actif (stop_urgence, /pause, coupe-circuit R déjà
       déclenché) : vérifié AVANT decide_entry, ne consomme pas de calcul
       de sizing pour un signal qui sera de toute façon rejeté.
    2. Plafond d'exposition simultanée (§2.3, 10% de l'enveloppe) :
       vérifié APRÈS decide_entry, seul moment où le risque incrémental
       (risk_amount_eur) de CE signal est connu.

    `_compute_guaranteed_stop_adjustment` est appelée AVANT decide_entry
    (corrigé le 21/08/2026, voir docs/DECISIONS.md) : le stop réellement
    utilisé pour la décision (péremption ET sizing) est désormais le
    stop EFFECTIF (élargi si le broker l'exige), jamais le stop brut du
    signal — incohérence trouvée en investiguant pourquoi aucun signal
    GOLD n'avait jamais atteint la logique de stop garanti (la tolérance
    de péremption, calculée sur un stop de 2-3 points, rejetait presque
    tout avant même d'atteindre l'élargissement à ~45 points). Ceci
    supprime aussi le besoin d'un second passage risk_engine après coup :
    le sizing est correct dès le premier appel à decide_entry."""
    asset = signal_row["actif"]
    epic = asset
    now = _now()

    blocked, block_reason = circuit_breaker_store.is_asset_blocked(
        db_path, asset, signal_row["source"], datetime.now(timezone.utc), bot_token, chat_id,
    )
    if blocked:
        with connection_scope(db_path) as conn:
            conn.execute(
                "INSERT INTO risk_decisions (signal_id, decided_at, approved, reason, detail, units, risk_amount_eur) "
                "VALUES (?, ?, 0, 'circuit_breaker_blocked', ?, NULL, NULL)",
                (signal_row["id"], now, f"Actif/global bloqué par un coupe-circuit : {block_reason}"),
            )
            conn.execute("UPDATE signals SET statut = 'rejete' WHERE id = ?", (signal_row["id"],))
        logger.info("Signal %s rejeté : coupe-circuit actif (%s)", signal_row["id"], block_reason)
        return None

    backtest_gate_detail = _check_backtest_confidence_gate(
        db_path, asset, signal_row["source"], risk_engine.caps.risk_percent_default,
    )
    if backtest_gate_detail is not None:
        with connection_scope(db_path) as conn:
            conn.execute(
                "INSERT INTO risk_decisions (signal_id, decided_at, approved, reason, detail, units, risk_amount_eur) "
                "VALUES (?, ?, 0, 'backtest_confidence_gate', ?, NULL, NULL)",
                (signal_row["id"], now, backtest_gate_detail),
            )
            conn.execute("UPDATE signals SET statut = 'rejete' WHERE id = ?", (signal_row["id"],))
        logger.info("Signal %s rejeté : %s", signal_row["id"], backtest_gate_detail)
        return None

    snapshot = get_price_snapshot(client, epic)

    # Spread réellement observé au moment du signal (§2.6, 24/08/2026,
    # voir docs/DECISIONS.md) — AVANT toute exécution, jamais reconstruit
    # après coup depuis un fill. Ferme le gap déjà documenté dans
    # confidence_scorer.py (market_snapshots.spread jamais alimenté pour
    # le live) : capturé pour CHAQUE signal qui atteint ce point,
    # approuvé ou non — un signal rejeté a quand même un spread réel au
    # moment où il a été évalué, utile pour l'éligibilité future du
    # couple (actif, source) même si ce signal précis ne devient jamais
    # un trade. Best-effort : un échec ici ne doit jamais empêcher
    # l'ouverture déjà en cours (même patron que record_align_matinale_
    # for_trade plus bas dans cette fonction).
    try:
        with connection_scope(db_path) as conn:
            conn.execute(
                "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) VALUES (?, ?, ?, ?, ?)",
                (signal_row["id"], snapshot.bid, snapshot.ask, round(snapshot.ask - snapshot.bid, 8), now),
            )
    except Exception:
        logger.exception("Échec de la capture du spread pour le signal %s — sans impact sur la suite", signal_row["id"])

    adjustment = _compute_guaranteed_stop_adjustment(
        client, epic, signal_row["sens"], signal_row["entree_min"], signal_row["stop_loss"],
    )

    decision = decide_entry(
        asset=asset, direction=signal_row["sens"], entry_price=signal_row["entree_min"],
        stop_price=adjustment.stop_price, confidence=signal_row["confiance"] or 0.0,
        current_price=snapshot.mid, market_status=snapshot.market_status,
        risk_engine=risk_engine, whitelist=whitelist, envelope_balance=envelope_manager.balance,
        confidence_threshold=confidence_threshold, go_nogo_ok=go_nogo_status.allowed,
    )

    if decision.approved:
        open_risk_eur = circuit_breaker_store.get_open_risk_eur(db_path, asset, signal_row["source"])
        if evaluate_exposure_cap(open_risk_eur, envelope_manager.balance, decision.risk_decision.risk_amount_eur):
            decision = EntryDecision(
                approved=False, validation=decision.validation, risk_decision=decision.risk_decision,
                detail=(
                    f"Rejeté : plafond d'exposition simultanée dépassé (§2.3) — "
                    f"engagé={open_risk_eur}€ + nouveau={decision.risk_decision.risk_amount_eur}€ "
                    f"> 10% de {envelope_manager.balance}€"
                ),
            )

    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO risk_decisions (signal_id, decided_at, approved, reason, detail, units, risk_amount_eur) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                signal_row["id"], now, int(decision.approved),
                decision.risk_decision.reason.value if decision.risk_decision and decision.risk_decision.reason else None,
                decision.detail,
                decision.risk_decision.units if decision.risk_decision else None,
                decision.risk_decision.risk_amount_eur if decision.risk_decision else None,
            ),
        )
        conn.execute("UPDATE signals SET statut = ? WHERE id = ?", ["approuve" if decision.approved else "rejete", signal_row["id"]])

    if not decision.approved:
        logger.info("Signal %s rejeté : %s", signal_row["id"], decision.detail)
        return None

    units, risk_amount_eur = decision.risk_decision.units, decision.risk_decision.risk_amount_eur
    if adjustment.widened:
        # Le stop budgété par le signal ne satisfait pas le minimum garanti
        # du broker — élargi au lieu d'être rejeté (décision assumée
        # d'Ismaël, 20/08/2026, remplace le rejet du 16/08/2026 : compte
        # démo, priorité à l'observation du signal exécuté plutôt qu'à la
        # fidélité parfaite au stop d'origine — voir docs/DECISIONS.md).
        # Le sizing (units/risk_amount_eur) ci-dessus est déjà celui du
        # stop élargi (decide_entry l'a reçu dès le départ, 21/08/2026) —
        # invariant #2 respecté sans second passage.
        logger.info(
            "Signal %s : stop garanti élargi %s -> %s, taille calculée directement dessus (%s unités)",
            signal_row["id"], signal_row["stop_loss"], adjustment.stop_price, units,
        )

    direction_api = DIRECTION_TO_API[signal_row["sens"]]
    try:
        result = client.place_limit_order(
            epic=epic, direction=direction_api, size=units,
            level=signal_row["entree_min"],
            guaranteed_stop=adjustment.stop_distance > 0, stop_distance=adjustment.stop_distance if adjustment.stop_distance > 0 else None,
        )
    except CapitalApiError:
        logger.exception("Échec du placement de l'ordre limite pour le signal %s", signal_row["id"])
        return None

    # Session de marché à l'ouverture (collecte uniquement, demande
    # explicite d'Ismaël, 20/08/2026 — voir docs/DECISIONS.md et
    # session_marker.py). Calculée sur `now`, le même horodatage que
    # `ouvert_at` : une seule source de vérité pour "l'heure d'ouverture",
    # jamais un second appel horloge qui pourrait diverger.
    session_marche = compute_market_session(datetime.fromisoformat(now).hour)

    regime_type = _REGIME_TYPE_BY_SOURCE.get(signal_row["source"])
    exit_type = _EXIT_TYPE_BY_SOURCE.get(signal_row["source"], "tp_partiel")
    timing_layer = _TIMING_LAYER_BY_SOURCE.get(signal_row["source"])

    with connection_scope(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, guaranteed_stop, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut, stop_elargi, stop_origine_signal, session_marche, "
            "regime_type, exit_type, timing_layer) "
            "VALUES (?, ?, ?, ?, 'demo', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'en_attente', ?, ?, ?, ?, ?, ?)",
            (
                signal_row["id"], result["deal_id"], signal_row["source"], asset, signal_row["sens"],
                units, signal_row["entree_min"], int(adjustment.stop_distance > 0),
                adjustment.stop_price, adjustment.stop_price, risk_amount_eur,
                (risk_amount_eur / envelope_manager.balance * 100) if envelope_manager.balance else 0.0,
                now, int(adjustment.widened), signal_row["stop_loss"] if adjustment.widened else None,
                session_marche, regime_type, exit_type, timing_layer,
            ),
        )
        trade_id = cursor.lastrowid

    # §3.8, variable #1 — collecte uniquement (invariant : n'influence
    # jamais decide_entry ci-dessus, appelé APRÈS que le trade est déjà
    # journalisé). Best-effort : un échec ici ne doit jamais remettre en
    # cause l'ouverture déjà actée, seule la collecte statistique est
    # perdue pour ce trade — voir docs/DECISIONS.md (même patron que
    # l'analyse post-trade best-effort dans _apply_management_action).
    try:
        record_align_matinale_for_trade(db_path, trade_id, asset, signal_row["sens"], now)
    except Exception:
        logger.exception("Échec de la collecte align_matinale pour le trade %s — sans impact sur l'ouverture", trade_id)

    logger.info("Ordre limite placé pour le signal %s : deal_id=%s", signal_row["id"], result["deal_id"])
    return result["deal_id"]


def cancel_stale_working_orders(db_path: str, client: CapitalClient, max_age_seconds: int = LIMIT_ORDER_EXPIRY_SECONDS) -> int:
    """Annule les ordres limite en attente depuis trop longtemps (§2.8,
    péremption). Retourne le nombre d'ordres annulés. N'affecte jamais
    une position déjà ouverte (uniquement /workingorders, pas /positions).

    Bug réel trouvé le 20/08/2026 (voir docs/DECISIONS.md) : cette
    fonction annulait bien l'ordre côté BROKER mais ne mettait jamais à
    jour `trades.statut` en base — le trade restait indéfiniment
    "en_attente", un trade fantôme qui bloquait silencieusement tout
    nouveau signal sur cet actif via `_has_active_hypothesis_signal_or_
    trade()` (Flux B). Corrigé : la ligne `trades` correspondante
    (rapprochée par `deal_id`, identifiant broker unique — aucun filtre
    par source nécessaire, contrairement à check_pending_fills) passe à
    `statut='annule'` UNIQUEMENT après confirmation de l'annulation côté
    broker (jamais en cas d'échec de `cancel_working_order` — pas de
    statut modifié sur une base incertaine, invariant #7)."""
    cancelled = 0
    now = datetime.now(timezone.utc)
    for order in client.get_working_orders():
        data = order.get("workingOrderData", {})
        created = data.get("createdDateUTC")
        if not created:
            continue
        created_at = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
        age = (now - created_at).total_seconds()
        if age > max_age_seconds:
            try:
                client.cancel_working_order(data["dealId"])
                cancelled += 1
                logger.info("Ordre limite périmé annulé : dealId=%s, âge=%.0fs", data["dealId"], age)
            except CapitalApiError:
                logger.exception("Échec d'annulation de l'ordre limite périmé %s", data.get("dealId"))
                continue
            with connection_scope(db_path) as conn:
                conn.execute(
                    "UPDATE trades SET statut = 'annule' WHERE deal_id = ? AND statut = 'en_attente'",
                    (data["dealId"],),
                )
    return cancelled


# ---------------------------------------------------------------------------
# Orchestration I/O — gestion des positions ouvertes
# ---------------------------------------------------------------------------

def _load_open_trade_state(conn, trade_row) -> OpenTradeState:
    """tp1/tp2 viennent du signal d'origine (signals.tp1/tp2 — jamais
    dupliqués sur trades), tp1_hit/tp2_hit/remaining_fraction sont
    dérivés de trade_partials (déjà les clôtures effectivement
    exécutées) plutôt que stockés comme un état séparé qui pourrait
    diverger de l'historique réel."""
    tp1 = tp2 = take_profit = None
    if trade_row["signal_id"] is not None:
        signal_row = conn.execute(
            "SELECT tp1, tp2, take_profit FROM signals WHERE id = ?", (trade_row["signal_id"],)
        ).fetchone()
        if signal_row is not None:
            tp1, tp2 = signal_row["tp1"], signal_row["tp2"]
            take_profit = signal_row["take_profit"]

    partials = conn.execute(
        "SELECT palier, fraction FROM trade_partials WHERE trade_id = ?", (trade_row["id"],)
    ).fetchall()
    tp1_hit = any(p["palier"] == "tp1" for p in partials)
    tp2_hit = any(p["palier"] == "tp2" for p in partials)
    remaining_fraction = round(1.0 - sum(p["fraction"] for p in partials), 10)

    return OpenTradeState(
        trade_id=trade_row["id"],
        deal_id=trade_row["deal_id"],
        asset=trade_row["actif"],
        source=trade_row["source"],
        direction=trade_row["direction"],
        entry_price=trade_row["prix_entree_reel"] or trade_row["prix_entree_prevu"],
        initial_stop_price=trade_row["stop_loss_initial"],
        stop_price=trade_row["stop_loss_courant"],
        tp1=tp1, tp2=tp2,
        tp1_hit=tp1_hit, tp2_hit=tp2_hit,
        remaining_fraction=remaining_fraction,
        guaranteed_stop=bool(trade_row["guaranteed_stop"]),
        take_profit=take_profit,
    )


def check_pending_fills(
    db_path: str, client: CapitalClient, sources: Optional[list] = None,
    source_filter: Optional[Callable[[str], bool]] = None,
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> int:
    """Détecte les ordres limite (statut='en_attente') qui ont été
    exécutés depuis le dernier passage.

    `sources` : si fourni, ne traite que les trades dont `source` est
    dans cette liste — évite tout recoupement entre boucles indépendantes
    qui tournent en parallèle sur la même base (voir manage_open_trades,
    docs/DECISIONS.md). `source_filter` : filtre Python alternatif, voir
    `_is_stationx_source` (docs/DECISIONS.md, 21/08/2026 — comportement
    historique de `run_executor_loop` : appelée SANS aucun filtre,
    traitait donc aussi les remplissages d'ordres d'autres sources).
    Sans filtre par défaut.

    `bot_token`/`chat_id` : notifie l'ouverture effective du trade (§7.2 —
    absent avant le 20/08/2026, voir docs/DECISIONS.md : un signal placé
    en ordre limite n'était jusque-là JAMAIS annoncé, même une fois
    rempli). Notifié ICI (au remplissage, pas à `open_signal`) parce que
    c'est le seul moment où `prix_entree_reel` est connu — un ordre
    limite placé mais jamais rempli (péremption §2.8) ne doit jamais
    apparaître comme "ouvert". Omis (None), le remplissage est quand même
    détecté et journalisé, seule la notification est sautée.

    Bug réel trouvé le 16/08/2026 pendant le test encadré : Capital.com
    N'attribue PAS le même dealId à l'ordre limite et à la position
    résultante — vérifié en observant un remplissage réel (voir
    docs/DECISIONS.md). Le dealId de la position est nouveau ; l'ancien
    dealId de l'ordre limite (celui stocké dans trades.deal_id au
    moment du placement) réapparaît côté position sous
    `position.workingOrderId`, jamais sous `position.dealId`. Le
    rapprochement se fait donc sur ce champ, et `trades.deal_id` est
    RÉÉCRIT avec le nouveau dealId de la position — indispensable, car
    toute gestion ultérieure (clôture, mise à jour de stop) référence la
    position par ce nouveau deal_id, jamais par celui de l'ordre.

    Retourne le nombre de trades passés à 'ouvert'."""
    positions = client.get_open_positions()
    positions_by_working_order_id = {p.get("position", {}).get("workingOrderId"): p for p in positions}
    working_order_ids = {o.get("workingOrderData", {}).get("dealId") for o in client.get_working_orders()}

    query = "SELECT * FROM trades WHERE statut = 'en_attente'"
    params: list = []
    if sources:
        query += f" AND source IN ({','.join('?' for _ in sources)})"
        params.extend(sources)

    filled = 0
    with connection_scope(db_path) as conn:
        pending = conn.execute(query, params).fetchall()
        if source_filter is not None:
            pending = [row for row in pending if source_filter(row["source"])]
        for trade_row in pending:
            order_deal_id = trade_row["deal_id"]
            if order_deal_id in working_order_ids:
                continue  # toujours en attente, rien à faire

            position = positions_by_working_order_id.get(order_deal_id)
            if position is None:
                continue  # ni en attente ni ouvert : probablement annulé (péremption) — laissé tel quel pour audit

            position_data = position.get("position", {})
            entry_level = position_data.get("level")
            new_deal_id = position_data.get("dealId")
            conn.execute(
                "UPDATE trades SET statut = 'ouvert', prix_entree_reel = ?, "
                "slippage_entree = ?, deal_id = ? WHERE id = ?",
                (
                    entry_level,
                    (entry_level - trade_row["prix_entree_prevu"]) if entry_level is not None else None,
                    new_deal_id, trade_row["id"],
                ),
            )
            filled += 1
            logger.info(
                "Ordre limite exécuté : trade_id=%s, deal_id ordre=%s -> deal_id position=%s, niveau=%s",
                trade_row["id"], order_deal_id, new_deal_id, entry_level,
            )
            if bot_token and chat_id:
                message = format_trade_opened_notification(
                    trade_row["actif"], _envelope_source_key(trade_row["source"]), trade_row["direction"],
                    entry_level, trade_row["stop_loss_initial"], trade_row["taille_initiale"],
                )
                send_notification(bot_token, chat_id, message)
    return filled


def manage_open_trades(
    db_path: str, client: CapitalClient, risk_engine: RiskEngine,
    envelope_managers: dict, envelope_ids: dict,
    include_sources: Optional[list] = None, exclude_sources: Optional[list] = None,
    source_filter: Optional[Callable[[str], bool]] = None,
    anthropic_client=None, bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> None:
    """Parcourt les trades ouverts, applique evaluate_position_management
    sur chacun, exécute les actions résultantes (clôture partielle/totale
    via l'API, mise à jour du stop).

    `envelope_managers`/`envelope_ids` : dict {(actif, "stationx"|
    "hypothesis"): CapitalManager / id} déjà chargés par l'appelant — le
    routage se fait sur (state.asset, _envelope_source_key(state.source)),
    jamais sur l'actif seul, pour ne jamais mélanger les résultats de
    deux sources sur le même actif (§2.11, Flux B — voir docs/DECISIONS.md).

    `include_sources`/`exclude_sources` : filtrent les trades gérés par
    cet appel (listes SQL IN/NOT IN) — indispensable depuis que plusieurs
    boucles indépendantes tournent en parallèle sur la même base :
    chacune ne doit gérer que ses propres trades, jamais ceux d'une
    autre boucle. Utilisé par les boucles "hypothèse" (une seule source
    précise à inclure, ex `include_sources=[HYPOTHESIS3_SOURCE]`).

    `source_filter` : filtre Python appliqué APRÈS la requête SQL,
    alternative à `exclude_sources` pour Station X — voir
    `_is_stationx_source` et docs/DECISIONS.md (21/08/2026, incident où
    `exclude_sources=[HYPOTHESIS_SOURCE]` avait oublié "hypothesis3").
    Combinable avec `include_sources`/`exclude_sources` mais pensé pour
    s'y substituer côté Station X. Aucun filtre par défaut (comportement
    historique, utilisé par les tests qui ne se soucient pas du
    multi-source).

    Le total de réserve (§2.3) n'est plus tenu en mémoire d'un trade à
    l'autre : chaque clôture complète relit `load_reserve_total(db_path)`
    juste avant d'écrire, pour rester correct même si Station X et le
    Flux B closent un trade gagnant au même moment dans deux process
    séparés (voir docs/DECISIONS.md — limite acceptée : une fenêtre de
    course résiduelle subsiste entre la lecture et l'écriture, jugée
    négligeable au volume de trades actuel, sans capital réel en jeu).

    `anthropic_client`/`bot_token`/`chat_id` : optionnels, transmis à
    trade_analyzer.analyze_closed_trade() sur toute clôture complète —
    omis (None), le trade se ferme quand même, seule l'analyse
    post-trade est sautée."""
    query = "SELECT * FROM trades WHERE statut = 'ouvert'"
    params: list = []
    if include_sources:
        query += f" AND source IN ({','.join('?' for _ in include_sources)})"
        params.extend(include_sources)
    if exclude_sources:
        query += f" AND source NOT IN ({','.join('?' for _ in exclude_sources)})"
        params.extend(exclude_sources)

    with connection_scope(db_path) as conn:
        open_trades = conn.execute(query, params).fetchall()

    if source_filter is not None:
        open_trades = [row for row in open_trades if source_filter(row["source"])]

    for trade_row in open_trades:
        try:
            with connection_scope(db_path) as conn:
                state = _load_open_trade_state(conn, trade_row)
            snapshot = get_price_snapshot(client, state.asset)
            # count : au moins DONCHIAN_PERIOD+1 (trailing Flux B) ET
            # 14+1 (ATR Station X) bougies — la même récupération sert
            # aux deux calculs, quelle que soit la source du trade.
            # resolution : par hypothèse (_TREND_CANDLE_RESOLUTION), jamais
            # "HOUR" en dur — voir son commentaire (bug du 21/08/2026,
            # corrigé avant le déploiement de l'Hypothèse #3 en M15).
            trend_resolution = _TREND_CANDLE_RESOLUTION.get(_envelope_source_key(state.source), "HOUR")
            candles = get_candles(client, state.asset, resolution=trend_resolution, count=DONCHIAN_PERIOD + 1)
            atr = compute_atr(candles, period=14)

            action = evaluate_position_management(state, snapshot.mid, atr, risk_engine, candles=candles)
            if action.action == ManagementActionType.NONE:
                continue

            _apply_management_action(
                db_path, client, state, action, envelope_managers, envelope_ids,
                risk_engine=risk_engine, current_price=snapshot.mid,
                anthropic_client=anthropic_client, bot_token=bot_token, chat_id=chat_id,
            )
        except Exception:
            logger.exception("Erreur non gérée en gérant le trade %s — passage au suivant", trade_row["id"])


# Codes structurés persistés dans trades.cloture_reason (20/08/2026, voir
# docs/DECISIONS.md) — jamais les libellés français directement en base,
# pour que metrics.py puisse filtrer par code sans dépendre d'un texte.
_CLOSE_REASON_LABELS = {
    "stop_initial": "stop initial",
    "stop_breakeven": "stop au breakeven",
    "trailing": "stop suiveur (trailing)",
    "stop_urgence": "arrêt d'urgence",
    "take_profit_fixe": "take-profit fixe (Hypothèse #4)",
}


def _infer_close_reason(action: "ManagementAction", state: OpenTradeState) -> str:
    """Code de raison d'une clôture totale (§7.2, §7.1) — reconstruit
    depuis l'état plutôt que stocké tel quel : `trade_partials.palier`
    vaut toujours "sl" pour toute CLOSE_FULL_STOP (stop initial, breakeven,
    trailing ET /stop_urgence confondus, voir son commentaire dans db.py),
    et `action.detail` ne distingue pas non plus les trois cas de stop
    "normal" (toujours "Stop touché (initial, breakeven ou trailing)" —
    voir _evaluate_position_management). Comparer state.stop_price à
    state.initial_stop_price/entry_price au moment de la clôture est le
    seul moyen fiable de savoir lequel des trois a réellement été touché ;
    /stop_urgence est détecté séparément et prioritaire (force_close_all_
    open_trades construit un prix de sortie au marché qui ne correspond à
    aucun des trois niveaux de stop, donc ne serait de toute façon jamais
    confondu — cette priorité est une garde explicite, pas un besoin
    strictement nécessaire).

    Retourne une clé de _CLOSE_REASON_LABELS, jamais un texte libre —
    l'appelant traduit pour l'affichage humain (notification), jamais
    l'inverse."""
    if action.action == ManagementActionType.CLOSE_FULL_TP:
        # Hypothèse #4 : clôture par cible atteinte, jamais par stop —
        # court-circuite la comparaison state.stop_price ci-dessous
        # (non pertinente ici, le stop de H4 n'a par construction jamais
        # bougé, voir OpenTradeState.take_profit).
        return "take_profit_fixe"
    if "urgence" in (action.detail or "").lower():
        return "stop_urgence"
    if state.stop_price == state.initial_stop_price:
        return "stop_initial"
    if state.stop_price == state.entry_price:
        return "stop_breakeven"
    return "trailing"


def _weighted_r_multiple_for_trade(db_path: str, trade_id: int) -> float:
    """R-multiple total pondéré sur l'ensemble des paliers déjà clos
    (§2.10, risk_engine.compute_weighted_r_multiple) — jamais le seul R du
    dernier palier. Bug réel trouvé le 20/08/2026 en câblant la
    notification de clôture (ci-dessous) : compute_weighted_r_multiple
    existait dans risk_engine.py, testée, mais jamais appelée depuis ce
    module — trades.r_multiple_total ne reflétait que le R du dernier
    palier fermé, pas le total pondéré malgré son nom. Voir docs/DECISIONS.md."""
    with connection_scope(db_path) as conn:
        partials = conn.execute(
            "SELECT fraction, r_atteint FROM trade_partials WHERE trade_id = ?", (trade_id,)
        ).fetchall()
    return compute_weighted_r_multiple([(p["fraction"], p["r_atteint"]) for p in partials])


def _apply_management_action(
    db_path, client, state, action, envelope_managers, envelope_ids,
    risk_engine=None, current_price=None,
    anthropic_client=None, bot_token=None, chat_id=None,
) -> None:
    if action.action == ManagementActionType.UPDATE_TRAILING_STOP:
        new_stop_price = action.new_stop_price
        if state.guaranteed_stop and risk_engine is not None and current_price is not None:
            # Plafonne le candidat de trailing au minimum garanti du broker
            # AVANT toute tentative — corrigé le 21/08/2026 (voir
            # docs/DECISIONS.md) : avant ce correctif, un candidat Donchian
            # plus serré que ce minimum était tenté tel quel, rejeté en
            # boucle indéfiniment (error.invalid.stoploss.minvalue) sans
            # jamais dégrader gracieusement — le trailing restait bloqué au
            # dernier niveau accepté, jamais réévalué. Réutilise
            # _compute_guaranteed_stop_adjustment (déjà utilisée à
            # l'ouverture), avec le prix COURANT comme référence — c'est
            # contre le marché courant, pas le prix d'entrée d'origine, que
            # le broker applique cette contrainte à un trade déjà ouvert.
            adjustment = _compute_guaranteed_stop_adjustment(
                client, state.asset, state.direction, current_price, new_stop_price,
            )
            if adjustment.widened:
                # Le plafond est un ÉLARGISSEMENT par rapport au candidat
                # Donchian brut — mais reste-t-il un RESSERREMENT par
                # rapport au stop actuellement en place ? Revalidé via
                # risk_engine.evaluate_stop_update (invariant #5, jamais
                # cette fonction seule) : si même le plafond n'améliore pas
                # le stop existant, aucune mise à jour n'est tentée plutôt
                # que d'échouer au broker pour rien.
                stop_decision = risk_engine.evaluate_stop_update(state.stop_price, adjustment.stop_price, state.direction)
                if not stop_decision.approved:
                    logger.info(
                        "Trailing plafonné au minimum garanti pour le trade %s (%s), mais n'améliore plus le stop actuel (%s) — inchangé",
                        state.trade_id, adjustment.stop_price, state.stop_price,
                    )
                    return
                logger.info(
                    "Trailing plafonné au minimum garanti broker pour le trade %s : %s -> %s (candidat brut %s)",
                    state.trade_id, state.stop_price, adjustment.stop_price, new_stop_price,
                )
                new_stop_price = adjustment.stop_price
        client.update_position_stop(state.deal_id, new_stop_price, guaranteed_stop=state.guaranteed_stop)
        with connection_scope(db_path) as conn:
            conn.execute("UPDATE trades SET stop_loss_courant = ? WHERE id = ?", (new_stop_price, state.trade_id))
        return

    # Clôtures (partielles ou totales)
    is_full_close = action.action in (ManagementActionType.CLOSE_FULL_STOP, ManagementActionType.CLOSE_FULL_TP)
    close_size = None if is_full_close else round(action.fraction_to_close * _initial_size(db_path, state.trade_id), 10)
    try:
        client.close_position(state.deal_id, size=close_size)
    except CapitalApiError:
        # Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : un stop
        # garanti s'exécute INSTANTANÉMENT côté broker dès que le prix le
        # touche, sans attendre notre prochain cycle de polling. Si notre
        # boucle détecte le même stop touché ensuite (course normale, pas
        # une anomalie), cet appel échoue en 404 "position introuvable" —
        # avant ce correctif, l'exception remontait telle quelle jusqu'au
        # `except Exception` générique de manage_open_trades, qui journalise
        # et passe au trade suivant SANS jamais mettre à jour `trades.statut`
        # : le trade restait "ouvert" indéfiniment (5 trades fantômes
        # trouvés et réconciliés manuellement ce jour-là, voir
        # docs/DECISIONS.md). Vérifié ici via un appel frais à
        # get_open_positions() (jamais une simple lecture du message
        # d'erreur, qui pourrait changer de format) : si la position
        # n'existe VRAIMENT plus, on procède quand même à la clôture en
        # base (au meilleur prix connu, action.exit_price) plutôt que de
        # rester bloqué — invariant #7, fail-safe, mais ici "ne rien faire"
        # laisserait un trade fantôme, pas une position protégée. Si la
        # position existe encore, l'erreur est réelle : ne jamais la
        # masquer, la relever telle quelle (comportement inchangé).
        still_open = any(
            p.get("position", {}).get("dealId") == state.deal_id for p in client.get_open_positions()
        )
        if still_open:
            raise
        logger.warning(
            "Position %s déjà fermée côté broker (stop garanti probable) — "
            "réconciliation de la base sur le dernier prix connu (%s)",
            state.deal_id, action.exit_price,
        )

    now = _now()
    palier = {
        ManagementActionType.CLOSE_FULL_STOP: "sl",
        ManagementActionType.CLOSE_PARTIAL_TP1: "tp1",
        ManagementActionType.CLOSE_PARTIAL_TP2: "tp2",
        ManagementActionType.CLOSE_FULL_TP: "tp",  # Hypothèse #4 — cible fixe unique, jamais "tp1"/"tp2" (Station X)
    }[action.action]
    source_label = _envelope_source_key(state.source)

    # Chaque connection_scope ci-dessous s'ouvre et se referme (commit)
    # séquentiellement, jamais imbriquée dans une autre : persist_trade_
    # result() gère sa propre transaction, l'appeler depuis l'intérieur
    # d'un `with connection_scope(...)` encore ouvert provoquait un
    # "database is locked" (bug réel trouvé par les tests, voir
    # docs/DECISIONS.md).
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (state.trade_id, palier, action.fraction_to_close, action.exit_price, action.r_multiple, action.detail, now),
        )
        if action.new_stop_price is not None:
            conn.execute("UPDATE trades SET stop_loss_courant = ? WHERE id = ?", (action.new_stop_price, state.trade_id))

    # Clôture partielle (§7.2, absent avant le 20/08/2026 — voir
    # docs/DECISIONS.md) : uniquement TP1/TP2 Station X — le Flux B n'a
    # structurellement aucune clôture partielle (pas de TP1/TP2, voir
    # docs/HYPOTHESES.md), sa seule notification est la clôture finale
    # ci-dessous (raison="stop suiveur (trailing)" quand le trailing
    # Donchian est touché).
    if not is_full_close and bot_token and chat_id:
        send_notification(
            bot_token, chat_id,
            format_trade_partial_notification(state.asset, source_label, palier.upper(), action.r_multiple),
        )

    if is_full_close:
        pnl_eur = _trade_pnl_eur(db_path, state.trade_id, action.r_multiple)
        r_multiple_total = _weighted_r_multiple_for_trade(db_path, state.trade_id)
        reason_code = _infer_close_reason(action, state)
        raison_label = _CLOSE_REASON_LABELS[reason_code]
        envelope_key = (state.asset, source_label)
        manager, envelope_id = envelope_managers[envelope_key], envelope_ids[envelope_key]
        balance_before = manager.balance
        reserve_before = load_reserve_total(db_path)
        reserve_share, reserve_after = apply_trade_result(manager, pnl_eur, reserve_before, note=action.detail)
        persist_trade_result(db_path, envelope_id, manager, state.trade_id, balance_before, reserve_share, reserve_after)

        with connection_scope(db_path) as conn:
            conn.execute(
                "UPDATE trades SET statut = 'ferme', ferme_at = ?, r_multiple_total = ?, pnl_net = ?, "
                "cloture_reason = ? WHERE id = ?",
                (now, r_multiple_total, pnl_eur, reason_code, state.trade_id),
            )

        if bot_token and chat_id:
            send_notification(
                bot_token, chat_id,
                format_trade_closed_notification(state.asset, source_label, r_multiple_total, raison_label, pnl_eur),
            )

        # La clôture est déjà journalisée ci-dessus avant cet appel :
        # un échec ici (LLM, réseau, garde-fou) ne doit jamais remettre
        # en cause l'enregistrement du trade fermé, seule l'analyse
        # post-trade est perdue pour ce cycle (bug réel trouvé le
        # 16/08/2026 pendant le test encadré : trade_analyzer.py était
        # entièrement construit et testé mais jamais appelé depuis
        # executor.py — voir docs/DECISIONS.md).
        try:
            analyze_closed_trade(
                db_path, state.trade_id, anthropic_client, bot_token, chat_id,
                source=source_label,
            )
        except Exception:
            logger.exception(
                "Échec de l'analyse post-trade pour le trade %s — clôture déjà journalisée, sans impact",
                state.trade_id,
            )


def _initial_size(db_path: str, trade_id: int) -> float:
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT taille_initiale FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return row["taille_initiale"]


def _trade_pnl_eur(db_path: str, trade_id: int, last_r_multiple: float) -> float:
    """PnL EUR total du trade = somme des risque_eur x r_atteint sur
    chaque palier déjà clos (trade_partials), y compris celui qu'on
    vient d'insérer dans le même appelant."""
    with connection_scope(db_path) as conn:
        trade = conn.execute("SELECT risque_eur FROM trades WHERE id = ?", (trade_id,)).fetchone()
        partials = conn.execute(
            "SELECT fraction, r_atteint FROM trade_partials WHERE trade_id = ?", (trade_id,)
        ).fetchall()
        risque_eur = trade["risque_eur"]
        return round(sum(p["fraction"] * p["r_atteint"] * risque_eur for p in partials), 2)


# ---------------------------------------------------------------------------
# /stop_urgence (§7.1) — fermeture forcée de toutes les positions ouvertes
# ---------------------------------------------------------------------------

def force_close_all_open_trades(
    db_path: str, client: CapitalClient,
    envelope_managers: dict, envelope_ids: dict,
    include_sources: Optional[list] = None, exclude_sources: Optional[list] = None,
    source_filter: Optional[Callable[[str], bool]] = None,
    anthropic_client=None, bot_token: Optional[str] = None, chat_id: Optional[str] = None,
    reason: str = "Arrêt d'urgence (/stop_urgence)",
) -> int:
    """Ferme immédiatement TOUTES les positions ouvertes du périmètre
    donné (source), au prix courant, et annule tous les ordres limite en
    attente du même périmètre. Retourne le nombre de positions fermées.

    `source_filter` : voir manage_open_trades — même rôle, mêmes
    raisons (docs/DECISIONS.md, 21/08/2026).

    Seule dérogation assumée à "ordres limite uniquement, jamais au
    marché" (§2.8) : /stop_urgence est une action de sécurité manuelle
    déclenchée par Ismaël via le bot de contrôle, pas l'exécution d'un
    signal — voir docs/DECISIONS.md. Réutilise _apply_management_action
    (même code que toute clôture SL/TP — envelope, réserve, journalisation,
    analyse post-trade), seule la CONSTRUCTION de l'action diffère
    (prix courant plutôt qu'un niveau de stop/TP déjà connu)."""
    query = "SELECT * FROM trades WHERE statut = 'ouvert'"
    params: list = []
    if include_sources:
        query += f" AND source IN ({','.join('?' for _ in include_sources)})"
        params.extend(include_sources)
    if exclude_sources:
        query += f" AND source NOT IN ({','.join('?' for _ in exclude_sources)})"
        params.extend(exclude_sources)
    with connection_scope(db_path) as conn:
        open_trades = conn.execute(query, params).fetchall()

    if source_filter is not None:
        open_trades = [row for row in open_trades if source_filter(row["source"])]

    closed = 0
    for trade_row in open_trades:
        try:
            with connection_scope(db_path) as conn:
                state = _load_open_trade_state(conn, trade_row)
            snapshot = get_price_snapshot(client, state.asset)
            r_multiple = compute_r_multiple(state.direction, state.entry_price, state.initial_stop_price, snapshot.mid)
            action = ManagementAction(
                action=ManagementActionType.CLOSE_FULL_STOP,
                fraction_to_close=state.remaining_fraction,
                exit_price=snapshot.mid,
                r_multiple=r_multiple,
                detail=reason,
            )
            _apply_management_action(
                db_path, client, state, action, envelope_managers, envelope_ids,
                anthropic_client=anthropic_client, bot_token=bot_token, chat_id=chat_id,
            )
            closed += 1
        except Exception:
            logger.exception("Échec de fermeture d'urgence du trade %s — passage au suivant", trade_row["id"])

    pending_query = "SELECT * FROM trades WHERE statut = 'en_attente'"
    pending_params: list = []
    if include_sources:
        pending_query += f" AND source IN ({','.join('?' for _ in include_sources)})"
        pending_params.extend(include_sources)
    if exclude_sources:
        pending_query += f" AND source NOT IN ({','.join('?' for _ in exclude_sources)})"
        pending_params.extend(exclude_sources)
    with connection_scope(db_path) as conn:
        pending = conn.execute(pending_query, pending_params).fetchall()
    if source_filter is not None:
        pending = [row for row in pending if source_filter(row["source"])]
    for trade_row in pending:
        try:
            client.cancel_working_order(trade_row["deal_id"])
            logger.info("Ordre limite annulé (arrêt d'urgence) : trade_id=%s", trade_row["id"])
        except CapitalApiError:
            logger.exception("Échec d'annulation d'urgence de l'ordre du trade %s", trade_row["id"])

    return closed


# ---------------------------------------------------------------------------
# Boucle continue — point d'entrée production
# ---------------------------------------------------------------------------

# Codée en dur, jamais dérivée de config.capital_environment : ce module
# ne doit structurellement jamais pouvoir toucher l'API réelle, même si
# la config était mal positionnée (invariant #4 — le passage en réel est
# verrouillé par code, pas par discipline ; ici, il n'existe simplement
# aucun chemin de code vers l'URL réelle).
_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"


def run_executor_loop(config, db_path: str, interval_seconds: int = 30, startup_offset_seconds: int = 0) -> None:
    """Boucle continue : place les signaux validés en attente, détecte
    les remplissages, gère les positions ouvertes (TP/SL/trailing),
    annule les ordres limite périmés. Mode démo exclusivement.

    go_nogo.py n'est PAS appelé ici : son verrou (§4.9) porte sur le
    passage en RÉEL, pas sur l'exécution démo — le diagramme
    d'architecture du CDC (§4.1) ne place le verrou Go/No-Go que sur la
    branche "EXÉCUTION RÉELLE", jamais sur "EXÉCUTION DÉMO continue".
    Appeler evaluate_go_nogo() ici bloquerait à tort CHAQUE entrée démo
    (CAPITAL_ENVIRONMENT="demo" ne satisfait jamais sa condition
    "== live"). GoNoGoStatus(allowed=True) est donc construit
    explicitement pour ce contexte démo, voir docs/DECISIONS.md.

    Fail-safe (invariant #7) : une exception non gérée dans le corps de
    la boucle est journalisée et interrompt seulement l'itération
    courante — jamais le process : abandonner la surveillance des
    positions déjà ouvertes serait plus dangereux que de réessayer au
    prochain cycle.

    `startup_offset_seconds` (défaut 0, 24/08/2026, voir
    docs/DECISIONS.md) : pause fixe unique avant le premier appel réseau
    — même mécanisme d'échelonnement que `technical_strategy_executor.
    run_technical_strategy_loop` (voir sa docstring), pour que les 6
    process de production ne cognent plus l'API Capital.com au même
    instant depuis la même IP."""
    import time

    import anthropic

    from src.asset_whitelist import build_asset_whitelist
    from src.config import ConfigError
    from src.market_data import get_eur_conversion_rate

    # Ciblage explicite de compte (incident réel du 20/08/2026, voir
    # docs/DECISIONS.md) : le compte "préféré" par défaut d'un
    # identifiant Capital.com est un état PARTAGÉ entre toutes les clés
    # API de cet identifiant — a basculé silencieusement vers un compte
    # vide dès la création d'un nouveau compte démo sur la plateforme.
    # Échec explicite ici plutôt qu'un démarrage qui semblerait réussir
    # sur le mauvais compte (fail-safe, invariant #7) — ne bloque QUE ce
    # process, pas telegram_listener/control_bot qui n'en ont pas besoin.
    if not config.capital_account_id:
        raise ConfigError(
            "CAPITAL_ACCOUNT_ID manquant — requis pour cibler explicitement le compte "
            "(jamais le compte \"préféré\", instable, voir docs/DECISIONS.md du 20/08/2026)"
        )

    time.sleep(startup_offset_seconds)

    client = CapitalClient(config.capital_api_key, config.capital_identifier, config.capital_api_password, _DEMO_BASE_URL)
    client.login()
    client.switch_account(config.capital_account_id)
    anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    caps = RiskCaps(
        risk_percent_default=config.risk_percent_default,
        risk_percent_boosted=config.risk_percent_boosted,
        envelope_initial=config.envelope_initial,
    )
    usd_to_eur = get_eur_conversion_rate(client, "USD")
    jpy_to_eur = get_eur_conversion_rate(client, "JPY")
    whitelist = build_asset_whitelist(usd_to_eur, jpy_to_eur)
    risk_engine = RiskEngine(caps=caps, whitelist=whitelist)
    go_nogo_status = GoNoGoStatus(allowed=True, reason="mode démo — verrou réel non applicable (§4.1 du CDC)")

    envelope_managers, envelope_ids = {}, {}
    for asset in whitelist:
        envelope_id, manager = load_or_create_envelope(db_path, asset, "demo", caps.envelope_initial, source="stationx")
        key = (asset, "stationx")
        envelope_ids[key], envelope_managers[key] = envelope_id, manager

    logger.info("Démarrage de la boucle d'exécution démo Station X (intervalle=%ds, %d actifs)", interval_seconds, len(whitelist))
    process_name = "executor"

    def _stationx_filter(source: str) -> bool:
        return _is_stationx_source(source, config.telegram_channel)

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Surcouche anomalie système (§2.7) : sonde de connectivité
            # légère à chaque cycle plutôt que d'instrumenter chaque appel
            # métier individuellement (écart documenté, voir
            # docs/DECISIONS.md) — 3 échecs consécutifs de CETTE sonde
            # déclenchent la pause générale des entrées.
            #
            # Capture aussi requests.exceptions.RequestException, pas
            # seulement CapitalApiError : un ConnectionError/
            # RemoteDisconnected brut (coupure réseau côté Capital.com,
            # observé en production le 20/08/2026) n'est PAS enveloppé en
            # CapitalApiError par capital_client.py (qui ne traduit que
            # requests.HTTPError) — avant ce correctif, ces pannes
            # tombaient dans le except Exception générique du bas de
            # boucle sans jamais incrémenter le compteur d'anomalie,
            # rendant la surcouche §2.7 aveugle à ce mode de panne
            # précis (voir docs/DECISIONS.md).
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

            circuit_breaker_store.check_channel_inactivity(db_path, now, config.telegram_bot_token, config.telegram_chat_id)

            # /stop_urgence (§7.1) : fermeture forcée une seule fois par
            # activation (voir circuit_breaker_store.get_unhandled_stop_
            # urgence_event_id) — les itérations suivantes n'ont rien à
            # fermer (déjà fait) et les nouvelles entrées restent bloquées
            # par is_asset_blocked tant que /reprendre n'a pas été envoyé.
            stop_event_id = circuit_breaker_store.get_unhandled_stop_urgence_event_id(db_path, process_name)
            if stop_event_id is not None:
                closed = force_close_all_open_trades(
                    db_path, client, envelope_managers, envelope_ids,
                    source_filter=_stationx_filter,
                    anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
                )
                circuit_breaker_store.mark_stop_urgence_handled(db_path, process_name, stop_event_id)
                logger.warning("Arrêt d'urgence traité : %d position(s) Station X fermée(s)", closed)
                time.sleep(interval_seconds)
                continue

            check_pending_fills(
                db_path, client, source_filter=_stationx_filter,
                bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
            )
            cancel_stale_working_orders(db_path, client)

            # Ne garde que Station X (docs/DECISIONS.md, 21/08/2026) :
            # _is_stationx_source exclut par défaut toute source
            # hypothèse, existante ou future, jamais une liste figée à
            # tenir à jour (incident réel — "hypothesis3" avait été
            # oubliée d'une liste d'exclusion analogue).
            with connection_scope(db_path) as conn:
                all_pending_signals = conn.execute(
                    "SELECT * FROM signals WHERE statut = 'a_valider'"
                ).fetchall()
            pending_signals = [row for row in all_pending_signals if _stationx_filter(row["source"])]
            for signal_row in pending_signals:
                key = (signal_row["actif"], "stationx")
                if key not in envelope_managers:
                    continue  # actif hors liste blanche courante, laissé tel quel pour audit
                open_signal(
                    db_path, client, signal_row, risk_engine, whitelist,
                    envelope_managers[key], envelope_ids[key],
                    config.confidence_threshold, go_nogo_status,
                    config.telegram_bot_token, config.telegram_chat_id,
                )

            manage_open_trades(
                db_path, client, risk_engine, envelope_managers, envelope_ids,
                source_filter=_stationx_filter,
                anthropic_client=anthropic_client, bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
            )
        except Exception:
            logger.exception("Erreur non gérée dans la boucle d'exécution — nouvelle tentative au prochain cycle")

        time.sleep(interval_seconds)


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_executor_loop(app_config, db_path=app_config.db_path)
