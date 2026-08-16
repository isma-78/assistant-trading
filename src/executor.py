"""
executor.py — Exécution démo autonome des signaux Station X (palier P2).
Ouvre, gère (TP1/TP2/TP3, trailing ATR sur TP3, resserrement de stop) et
clôture des positions sur le compte DÉMO Capital.com. Aucun euro réel en
jeu (§4.8 : le réel reste hors périmètre avant Porte A/B, verrouillé par
go_nogo.py — ce module n'a même pas accès à un client configuré sur
l'environnement "live").

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
from typing import Optional, Tuple

from src.capital_client import CapitalApiError, CapitalClient
from src.capital_manager import CapitalManager, apply_trade_result
from src.db import connection_scope
from src.envelope_store import load_or_create_envelope, load_reserve_total, persist_trade_result
from src.go_nogo import GoNoGoStatus
from src.market_data import compute_atr, get_candles, get_price_snapshot
from src.risk_engine import (
    ExistingPosition,
    RiskCaps,
    RiskDecision,
    RiskEngine,
    TradeSignal,
    compute_r_multiple,
)
from src.trade_analyzer import analyze_closed_trade
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


@dataclass(frozen=True)
class OpenTradeState:
    trade_id: int
    deal_id: str
    asset: str
    direction: str  # "long" | "short"
    entry_price: float
    initial_stop_price: float  # risque initial, jamais modifié (base du R-multiple, §2.1)
    stop_price: float          # stop courant (resserré au fil du temps)
    tp1: Optional[float]
    tp2: Optional[float]
    tp1_hit: bool
    tp2_hit: bool
    remaining_fraction: float  # fraction de la taille initiale encore ouverte


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
) -> ManagementAction:
    """Point d'entrée unique de gestion d'une position ouverte. Ne lève
    jamais d'exception : toute erreur interne devient NONE (aucune
    fermeture, aucune mise à jour) plutôt que d'agir sur une base
    incertaine — nuance du fail-safe (invariant #7) documentée dans
    docs/DECISIONS.md : ici, "ne rien faire" protège la position, ce
    n'est pas un renoncement silencieux à un ordre."""
    try:
        return _evaluate_position_management(state, current_price, atr, risk_engine)
    except Exception:
        logger.exception("Erreur interne dans evaluate_position_management (trade_id=%s)", state.trade_id)
        return ManagementAction(action=ManagementActionType.NONE, detail="Erreur interne, aucune action cette itération")


def _evaluate_position_management(
    state: OpenTradeState, current_price: float, atr: Optional[float], risk_engine: RiskEngine,
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

    return ManagementAction(action=ManagementActionType.NONE, detail="Aucune condition remplie")


# ---------------------------------------------------------------------------
# Orchestration I/O — ouverture d'un signal validé
# ---------------------------------------------------------------------------

def _compute_guaranteed_stop_distance(client: CapitalClient, epic: str, entry_price: float, stop_price: float) -> Optional[float]:
    """Ce compte démo exige un stop garanti sur certains instruments —
    observé sur BTCUSD/ETHUSD dès le palier P0, confirmé aussi sur
    EURUSD lors de la validation des ordres limite au palier P2 (pas
    seulement les cryptos, voir docs/DECISIONS.md). Calcule la distance
    à partir du stop déjà dimensionné par risk_engine.

    Si le minimum imposé par le broker est plus large que ce stop,
    retourne None plutôt que d'élargir silencieusement le stop (ce qui
    augmenterait le risque réel au-delà de ce que risk_engine a
    budgété) — l'appelant doit alors rejeter l'entrée, jamais
    compromettre le sizing pour la faire passer.

    Retourne 0.0 si aucun stop garanti n'est requis pour cet instrument
    (guaranteed_stop=False côté appelant dans ce cas)."""
    market = client.get_market_snapshot(epic)
    dealing_rules = market.get("dealingRules", {})
    min_gs = dealing_rules.get("minGuaranteedStopDistance", {})
    if not min_gs.get("value"):
        return 0.0

    if min_gs.get("unit") == "PERCENTAGE":
        min_distance = entry_price * (min_gs["value"] / 100.0)
    else:
        min_distance = min_gs["value"]

    stop_distance = abs(entry_price - stop_price)
    if stop_distance < min_distance:
        return None
    return stop_distance


def open_signal(
    db_path: str, client: CapitalClient, signal_row, risk_engine: RiskEngine, whitelist: dict,
    envelope_manager: CapitalManager, envelope_id: int, confidence_threshold: float, go_nogo_status: GoNoGoStatus,
) -> Optional[str]:
    """Traite un signal statut='a_valider' : valide, dimensionne, place
    un ordre LIMITE (§2.8), journalise. Retourne le deal_id si un ordre a
    été placé, None sinon (rejet quelque niveau que ce soit — toujours
    journalisé dans risk_decisions, jamais silencieux)."""
    asset = signal_row["actif"]
    epic = asset
    snapshot = get_price_snapshot(client, epic)

    decision = decide_entry(
        asset=asset, direction=signal_row["sens"], entry_price=signal_row["entree_min"],
        stop_price=signal_row["stop_loss"], confidence=signal_row["confiance"] or 0.0,
        current_price=snapshot.mid, market_status=snapshot.market_status,
        risk_engine=risk_engine, whitelist=whitelist, envelope_balance=envelope_manager.balance,
        confidence_threshold=confidence_threshold, go_nogo_ok=go_nogo_status.allowed,
    )

    now = _now()
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

    stop_distance = _compute_guaranteed_stop_distance(client, epic, signal_row["entree_min"], signal_row["stop_loss"])
    if stop_distance is None:
        logger.warning(
            "Signal %s : stop budgété (%s) plus serré que le stop garanti minimum de %s, rejeté sans élargir le risque",
            signal_row["id"], abs(signal_row["entree_min"] - signal_row["stop_loss"]), epic,
        )
        with connection_scope(db_path) as conn:
            conn.execute("UPDATE signals SET statut = 'rejete' WHERE id = ?", (signal_row["id"],))
        return None

    direction_api = DIRECTION_TO_API[signal_row["sens"]]
    try:
        result = client.place_limit_order(
            epic=epic, direction=direction_api, size=decision.risk_decision.units,
            level=signal_row["entree_min"],
            guaranteed_stop=stop_distance > 0, stop_distance=stop_distance if stop_distance > 0 else None,
        )
    except CapitalApiError:
        logger.exception("Échec du placement de l'ordre limite pour le signal %s", signal_row["id"])
        return None

    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, ?, ?, ?, 'demo', ?, ?, ?, ?, ?, ?, ?, ?, 'en_attente')",
            (
                signal_row["id"], result["deal_id"], signal_row["source"], asset, signal_row["sens"],
                decision.risk_decision.units, signal_row["entree_min"], signal_row["stop_loss"],
                signal_row["stop_loss"], decision.risk_decision.risk_amount_eur,
                (decision.risk_decision.risk_amount_eur / envelope_manager.balance * 100) if envelope_manager.balance else 0.0,
                now,
            ),
        )

    logger.info("Ordre limite placé pour le signal %s : deal_id=%s", signal_row["id"], result["deal_id"])
    return result["deal_id"]


def cancel_stale_working_orders(db_path: str, client: CapitalClient, max_age_seconds: int = LIMIT_ORDER_EXPIRY_SECONDS) -> int:
    """Annule les ordres limite en attente depuis trop longtemps (§2.8,
    péremption). Retourne le nombre d'ordres annulés. N'affecte jamais
    une position déjà ouverte (uniquement /workingorders, pas /positions)."""
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
    tp1 = tp2 = None
    if trade_row["signal_id"] is not None:
        signal_row = conn.execute(
            "SELECT tp1, tp2 FROM signals WHERE id = ?", (trade_row["signal_id"],)
        ).fetchone()
        if signal_row is not None:
            tp1, tp2 = signal_row["tp1"], signal_row["tp2"]

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
        direction=trade_row["direction"],
        entry_price=trade_row["prix_entree_reel"] or trade_row["prix_entree_prevu"],
        initial_stop_price=trade_row["stop_loss_initial"],
        stop_price=trade_row["stop_loss_courant"],
        tp1=tp1, tp2=tp2,
        tp1_hit=tp1_hit, tp2_hit=tp2_hit,
        remaining_fraction=remaining_fraction,
    )


def check_pending_fills(db_path: str, client: CapitalClient) -> int:
    """Détecte les ordres limite (statut='en_attente') qui ont été
    exécutés depuis le dernier passage.

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

    filled = 0
    with connection_scope(db_path) as conn:
        pending = conn.execute("SELECT * FROM trades WHERE statut = 'en_attente'").fetchall()
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
    return filled


def manage_open_trades(
    db_path: str, client: CapitalClient, risk_engine: RiskEngine,
    envelope_managers: dict, envelope_ids: dict, reserve_total: float,
    anthropic_client=None, bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> float:
    """Parcourt les trades ouverts, applique evaluate_position_management
    sur chacun, exécute les actions résultantes (clôture partielle/totale
    via l'API, mise à jour du stop). Retourne le nouveau total de
    réserve (§2.3) après application des trades clos pendant ce passage.
    `envelope_managers`/`envelope_ids` : dict {actif: (CapitalManager,
    envelope_id)} déjà chargés par l'appelant. `anthropic_client`/
    `bot_token`/`chat_id` : optionnels, transmis à trade_analyzer.
    analyze_closed_trade() sur toute clôture complète — omis (None), le
    trade se ferme quand même, seule l'analyse post-trade est sautée."""
    with connection_scope(db_path) as conn:
        open_trades = conn.execute("SELECT * FROM trades WHERE statut = 'ouvert'").fetchall()

    for trade_row in open_trades:
        try:
            with connection_scope(db_path) as conn:
                state = _load_open_trade_state(conn, trade_row)
            snapshot = get_price_snapshot(client, state.asset)
            candles = get_candles(client, state.asset, resolution="HOUR", count=20)
            atr = compute_atr(candles, period=14)

            action = evaluate_position_management(state, snapshot.mid, atr, risk_engine)
            if action.action == ManagementActionType.NONE:
                continue

            reserve_total = _apply_management_action(
                db_path, client, state, action, envelope_managers, envelope_ids, reserve_total,
                anthropic_client, bot_token, chat_id,
            )
        except Exception:
            logger.exception("Erreur non gérée en gérant le trade %s — passage au suivant", trade_row["id"])

    return reserve_total


def _apply_management_action(
    db_path, client, state, action, envelope_managers, envelope_ids, reserve_total,
    anthropic_client=None, bot_token=None, chat_id=None,
) -> float:
    if action.action == ManagementActionType.UPDATE_TRAILING_STOP:
        client.update_position_stop(state.deal_id, action.new_stop_price)
        with connection_scope(db_path) as conn:
            conn.execute("UPDATE trades SET stop_loss_courant = ? WHERE id = ?", (action.new_stop_price, state.trade_id))
        return reserve_total

    # Clôtures (partielles ou totales)
    is_full_close = action.action == ManagementActionType.CLOSE_FULL_STOP
    close_size = None if is_full_close else round(action.fraction_to_close * _initial_size(db_path, state.trade_id), 10)
    client.close_position(state.deal_id, size=close_size)

    now = _now()
    palier = {
        ManagementActionType.CLOSE_FULL_STOP: "sl",
        ManagementActionType.CLOSE_PARTIAL_TP1: "tp1",
        ManagementActionType.CLOSE_PARTIAL_TP2: "tp2",
    }[action.action]

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

    if is_full_close:
        pnl_eur = _trade_pnl_eur(db_path, state.trade_id, action.r_multiple)
        manager, envelope_id = envelope_managers[state.asset], envelope_ids[state.asset]
        balance_before = manager.balance
        reserve_share, reserve_total = apply_trade_result(manager, pnl_eur, reserve_total, note=action.detail)
        persist_trade_result(db_path, envelope_id, manager, state.trade_id, balance_before, reserve_share, reserve_total)

        with connection_scope(db_path) as conn:
            conn.execute(
                "UPDATE trades SET statut = 'ferme', ferme_at = ?, r_multiple_total = ?, pnl_net = ? WHERE id = ?",
                (now, action.r_multiple, pnl_eur, state.trade_id),
            )

        # La clôture est déjà journalisée ci-dessus avant cet appel :
        # un échec ici (LLM, réseau, garde-fou) ne doit jamais remettre
        # en cause l'enregistrement du trade fermé, seule l'analyse
        # post-trade est perdue pour ce cycle (bug réel trouvé le
        # 16/08/2026 pendant le test encadré : trade_analyzer.py était
        # entièrement construit et testé mais jamais appelé depuis
        # executor.py — voir docs/DECISIONS.md).
        try:
            analyze_closed_trade(db_path, state.trade_id, anthropic_client, bot_token, chat_id)
        except Exception:
            logger.exception(
                "Échec de l'analyse post-trade pour le trade %s — clôture déjà journalisée, sans impact",
                state.trade_id,
            )

    return reserve_total


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
# Boucle continue — point d'entrée production
# ---------------------------------------------------------------------------

# Codée en dur, jamais dérivée de config.capital_environment : ce module
# ne doit structurellement jamais pouvoir toucher l'API réelle, même si
# la config était mal positionnée (invariant #4 — le passage en réel est
# verrouillé par code, pas par discipline ; ici, il n'existe simplement
# aucun chemin de code vers l'URL réelle).
_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"


def run_executor_loop(config, db_path: str, interval_seconds: int = 30) -> None:
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
    prochain cycle."""
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
    whitelist = build_asset_whitelist(usd_to_eur, jpy_to_eur)
    risk_engine = RiskEngine(caps=caps, whitelist=whitelist)
    go_nogo_status = GoNoGoStatus(allowed=True, reason="mode démo — verrou réel non applicable (§4.1 du CDC)")

    envelope_managers, envelope_ids = {}, {}
    for asset in whitelist:
        envelope_id, manager = load_or_create_envelope(db_path, asset, "demo", caps.envelope_initial)
        envelope_ids[asset], envelope_managers[asset] = envelope_id, manager
    reserve_total = load_reserve_total(db_path)

    logger.info("Démarrage de la boucle d'exécution démo (intervalle=%ds, %d actifs)", interval_seconds, len(whitelist))

    while True:
        try:
            check_pending_fills(db_path, client)
            cancel_stale_working_orders(db_path, client)

            with connection_scope(db_path) as conn:
                pending_signals = conn.execute("SELECT * FROM signals WHERE statut = 'a_valider'").fetchall()
            for signal_row in pending_signals:
                asset = signal_row["actif"]
                if asset not in envelope_managers:
                    continue  # actif hors liste blanche courante, laissé tel quel pour audit
                open_signal(
                    db_path, client, signal_row, risk_engine, whitelist,
                    envelope_managers[asset], envelope_ids[asset],
                    config.confidence_threshold, go_nogo_status,
                )

            reserve_total = manage_open_trades(
                db_path, client, risk_engine, envelope_managers, envelope_ids, reserve_total,
                anthropic_client, config.telegram_bot_token, config.telegram_chat_id,
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
