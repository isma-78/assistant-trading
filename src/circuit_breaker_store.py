"""
circuit_breaker_store.py — Persistance et orchestration I/O des
coupe-circuits (§2.7) et du contrôle manuel minimal (§7.1, premier lot :
/stop_urgence, /pause, /reprendre, /etat). Fait le pont entre
circuit_breaker.py (logique pure, 100% couverte) et la base SQLite +
les notifications Telegram, même séparation que
risk_engine.py/envelope_store.py.

Aucune décision de risque ici : ce module lit/écrit l'état déjà décidé
par circuit_breaker.py, il ne recalcule aucun seuil.

Coupe-circuits par actif scopés (actif, source) — écart assumé par
rapport au §2.7 littéral qui ne mentionne pas "source" (notion
introduite après coup par le Flux B, palier P2.5) : sans ce scoping, une
série de pertes du Flux B sur EURUSD bloquerait à tort Station X sur le
même actif, et inversement — incohérent avec la séparation déjà
appliquée aux enveloppes (`envelopes.source`). Voir docs/DECISIONS.md.

Les commandes /pause et /reprendre du bot de contrôle, elles, portent
uniquement sur l'actif (jamais la source, §7.1 ne distingue pas) : elles
s'appliquent aux DEUX sources d'un même actif à la fois.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from src.audit_notifier import send_notification
from src.circuit_breaker import (
    evaluate_api_error_streak,
    evaluate_breadth_pause,
    evaluate_circuit_breakers,
    is_channel_inactive,
)
from src.db import connection_scope

logger = logging.getLogger(__name__)

# Même normalisation que executor._envelope_source_key : trades.source
# stocke la valeur brute du canal Telegram pour Station X (jamais la
# chaîne littérale "stationx"), voir docs/DECISIONS.md. Dupliquée plutôt
# qu'importée depuis executor.py pour éviter un import circulaire
# (executor.py importe ce module, pas l'inverse).
HYPOTHESIS_SOURCE = "hypothesis"


def _normalize_source(source: str) -> str:
    return HYPOTHESIS_SOURCE if source == HYPOTHESIS_SOURCE else "stationx"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Lecture d'historique pour le calcul des R
# ---------------------------------------------------------------------------

def get_closed_trades_r(db_path: str, asset: str, source: str) -> List[Tuple[str, float]]:
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT ferme_at, r_multiple_total, source FROM trades "
            "WHERE actif = ? AND statut = 'ferme' AND r_multiple_total IS NOT NULL",
            (asset,),
        ).fetchall()
    return [(row["ferme_at"], row["r_multiple_total"]) for row in rows if _normalize_source(row["source"]) == normalized]


def get_open_risk_eur(db_path: str, asset: str, source: str) -> float:
    """Approximation volontairement prudente de l'exposition ouverte
    (§2.3) : somme du risque budgété à l'ouverture (`risque_eur`) de
    chaque trade encore 'ouvert', sans le réduire après une clôture
    partielle (TP1/TP2) — surestime légèrement l'exposition réelle plutôt
    que de la sous-estimer, cohérent avec le parti pris fail-safe du
    reste du projet."""
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT risque_eur, source FROM trades WHERE actif = ? AND statut = 'ouvert'", (asset,)
        ).fetchall()
    return round(sum(row["risque_eur"] or 0.0 for row in rows if _normalize_source(row["source"]) == normalized), 2)


# ---------------------------------------------------------------------------
# Événements (déclenchement / effacement)
# ---------------------------------------------------------------------------

def _is_day_triggered_today(db_path: str, asset: str, source: str, today_iso: str) -> bool:
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM circuit_breaker_events WHERE scope = 'asset' AND actif = ? AND source = ? "
            "AND breaker_type = 'day_r' AND substr(triggered_at, 1, 10) = ? LIMIT 1",
            (asset, normalized, today_iso[:10]),
        ).fetchone()
    return row is not None


def _is_latched(db_path: str, asset: str, source: str, breaker_type: str) -> bool:
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM circuit_breaker_events WHERE scope = 'asset' AND actif = ? AND source = ? "
            "AND breaker_type = ? AND cleared_at IS NULL LIMIT 1",
            (asset, normalized, breaker_type),
        ).fetchone()
    return row is not None


def _is_manual_pause_active(db_path: str, asset: str, source: str) -> bool:
    """Pause manuelle (/pause) : posée par actif, source = NULL (les
    deux sources), jamais par source seule (§7.1 ne distingue pas)."""
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM circuit_breaker_events WHERE scope = 'asset' AND actif = ? "
            "AND breaker_type = 'manual_pause' AND cleared_at IS NULL LIMIT 1",
            (asset,),
        ).fetchone()
    return row is not None


def get_active_global_block(db_path: str) -> Optional[str]:
    """Retourne le breaker_type du blocage global actif le plus sévère
    (stop_urgence en premier), ou None si aucun n'est actif."""
    priority = ["stop_urgence", "manual_pause", "api_errors", "breadth"]
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT breaker_type FROM circuit_breaker_events "
            "WHERE scope = 'global' AND cleared_at IS NULL AND breaker_type != 'channel_inactive'"
        ).fetchall()
    active = {row["breaker_type"] for row in rows}
    for breaker_type in priority:
        if breaker_type in active:
            return breaker_type
    return None


def record_trigger(
    db_path: str, scope: str, actif: Optional[str], source: Optional[str],
    breaker_type: str, r_value: Optional[float], note: str,
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
    triggered_at: Optional[str] = None,
) -> int:
    """`triggered_at` : à fournir explicitement par un appelant qui
    évalue un `now` de référence (is_asset_blocked) — sinon l'horodatage
    stocké (temps réel de l'appel) peut diverger du `now` utilisé pour
    décider "aujourd'hui" côté circuit_breaker.py, cassant la
    déduplication de _is_day_triggered_today (bug réel trouvé par les
    tests : la date réelle d'exécution différait du `now` simulé,
    provoquant un re-déclenchement à chaque appel). Par défaut (commandes
    du bot de contrôle, sans notion de `now` simulé) : horodatage réel."""
    now = triggered_at or _now_iso()
    with connection_scope(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO circuit_breaker_events (scope, actif, source, breaker_type, triggered_at, r_value, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope, actif, source, breaker_type, now, r_value, note),
        )
        event_id = cursor.lastrowid

    logger.warning("Coupe-circuit déclenché : scope=%s actif=%s source=%s type=%s — %s", scope, actif, source, breaker_type, note)
    if bot_token and chat_id:
        label = f"{actif} ({source})" if actif else "GLOBAL"
        send_notification(
            bot_token, chat_id,
            f"\U0001F6D1 Coupe-circuit déclenché — {label}\nType : {breaker_type}\n{note}",
        )
    return event_id


def clear_breaker(db_path: str, actif: Optional[str], cleared_by: str) -> int:
    """Implémente /reprendre [actif] : efface TOUT événement non encore
    effacé pour cet actif (global si `actif` est None), quel que soit le
    type (manual_pause, week_r, drawdown_r, day_r, api_errors, breadth,
    stop_urgence) — reprise manuelle explicite, l'utilisateur assume la
    responsabilité de tout réactiver d'un coup plutôt que de devoir
    connaître le type exact de chaque déclenchement. Retourne le nombre
    d'événements effacés."""
    now = _now_iso()
    with connection_scope(db_path) as conn:
        if actif is None:
            cursor = conn.execute(
                "UPDATE circuit_breaker_events SET cleared_at = ?, cleared_by = ? "
                "WHERE scope = 'global' AND cleared_at IS NULL",
                (now, cleared_by),
            )
        else:
            cursor = conn.execute(
                "UPDATE circuit_breaker_events SET cleared_at = ?, cleared_by = ? "
                "WHERE scope = 'asset' AND actif = ? AND cleared_at IS NULL",
                (now, cleared_by, actif),
            )
        return cursor.rowcount


def trigger_manual_pause(db_path: str, actif: Optional[str], triggered_by: str, bot_token=None, chat_id=None) -> int:
    """Implémente /pause [actif] (§7.1)."""
    scope = "asset" if actif else "global"
    return record_trigger(
        db_path, scope, actif, None, "manual_pause",
        None, f"Pause manuelle déclenchée par {triggered_by}", bot_token, chat_id,
    )


def trigger_stop_urgence(db_path: str, triggered_by: str, bot_token=None, chat_id=None) -> int:
    """Implémente /stop_urgence (§7.1). Journalise uniquement le blocage
    global — la fermeture effective des positions ouvertes est déclenchée
    par les boucles executor.py / trend_executor.py au prochain cycle
    (voir executor.force_close_all_open_trades) : ce module ne touche
    jamais le broker directement (séparation contrôle/exécution, cohérent
    avec l'invariant #1)."""
    return record_trigger(
        db_path, "global", None, None, "stop_urgence",
        None, f"ARRÊT D'URGENCE déclenché par {triggered_by}", bot_token, chat_id,
    )


# ---------------------------------------------------------------------------
# Évaluation combinée — point d'entrée avant toute nouvelle entrée
# ---------------------------------------------------------------------------

def is_asset_blocked(
    db_path: str, asset: str, source: str, now: datetime,
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """Combine le blocage global (stop_urgence/pause/api_errors/breadth),
    la pause manuelle par actif, et les coupe-circuits R (§2.7) —
    évalués en direct puis journalisés/notifiés au premier franchissement.
    Point d'entrée unique appelé par executor.open_signal /
    trend_executor avant CHAQUE tentative d'ouverture."""
    global_block = get_active_global_block(db_path)
    if global_block is not None:
        return True, f"global:{global_block}"

    if _is_manual_pause_active(db_path, asset, source):
        return True, "manual_pause"

    closed_trades = get_closed_trades_r(db_path, asset, source)
    normalized = _normalize_source(source)
    day_flag = _is_day_triggered_today(db_path, asset, source, now.isoformat())
    week_latched = _is_latched(db_path, asset, source, "week_r")
    drawdown_latched = _is_latched(db_path, asset, source, "drawdown_r")

    status = evaluate_circuit_breakers(closed_trades, now, day_flag, week_latched, drawdown_latched)

    r_value_by_breaker = {
        "day_r": status.r_stats.day_r,
        "week_r": status.r_stats.week_r,
        "drawdown_r": status.r_stats.drawdown_from_peak_r,
    }
    for breaker_type in status.new_triggers:
        record_trigger(
            db_path, "asset", asset, normalized, breaker_type, r_value_by_breaker.get(breaker_type),
            f"Seuil {breaker_type} franchi (R={status.r_stats})",
            bot_token, chat_id, triggered_at=now.isoformat(),
        )
        _maybe_trigger_breadth_pause(db_path, now, bot_token, chat_id)

    if status.blocked:
        return True, "+".join(status.active_reasons) if status.active_reasons else "internal_error"
    return False, ""


def _maybe_trigger_breadth_pause(db_path: str, now: datetime, bot_token, chat_id) -> None:
    """§2.7 : coupe-circuit sur >= 5 actifs simultanément -> pause
    générale. Compte les actifs DISTINCTS avec au moins un breaker actif
    (n'importe quelle source) ; n'insère jamais un second événement
    'breadth' tant que le précédent n'est pas effacé (évite le spam à
    chaque nouvel actif qui franchit le seuil au-delà de 5). `now` : même
    référence temporelle que l'appelant (is_asset_blocked), jamais
    l'horloge réelle — même bug que record_trigger si on la recalculait
    ici (voir docs/DECISIONS.md)."""
    if get_active_global_block(db_path) == "breadth":
        return
    today = now.isoformat()[:10]
    with connection_scope(db_path) as conn:
        # week_r/drawdown_r : latchés (cleared_at IS NULL = toujours actifs).
        # day_r : jamais effacé (voir _is_day_triggered_today), donc scopé à
        # la date du jour explicitement plutôt que sur cleared_at, sous
        # peine de compter pour toujours un déclenchement d'il y a des
        # semaines comme "actif aujourd'hui".
        rows = conn.execute(
            "SELECT DISTINCT actif FROM circuit_breaker_events WHERE scope = 'asset' AND ("
            "  (breaker_type IN ('week_r', 'drawdown_r') AND cleared_at IS NULL)"
            "  OR (breaker_type = 'day_r' AND substr(triggered_at, 1, 10) = ?)"
            ")",
            (today,),
        ).fetchall()
    distinct_assets = {row["actif"] for row in rows}
    if evaluate_breadth_pause(len(distinct_assets)):
        record_trigger(
            db_path, "global", None, None, "breadth", None,
            f"Coupe-circuit actif sur {len(distinct_assets)} actifs simultanément — pause générale (bug probable)",
            bot_token, chat_id,
        )


# ---------------------------------------------------------------------------
# Surcouche anomalie système — erreurs API, inactivité du canal
# ---------------------------------------------------------------------------

def record_api_result(db_path: str, process_name: str, success: bool, bot_token=None, chat_id=None) -> int:
    """Streak d'erreurs API consécutives, persisté par process (survit à
    un redémarrage, contrairement à un compteur en mémoire) — §2.7."""
    key = f"api_error_streak:{process_name}"
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
        current = int(row["value"]) if row is not None else 0
        new_value = 0 if success else current + 1
        now = _now_iso()
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(new_value), now),
        )

    if not success and evaluate_api_error_streak(new_value) and get_active_global_block(db_path) != "api_errors":
        record_trigger(
            db_path, "global", None, None, "api_errors", None,
            f"{new_value} erreurs API consécutives ({process_name}) — pause générale des entrées",
            bot_token, chat_id,
        )
    return new_value


def check_channel_inactivity(db_path: str, now: datetime, bot_token: Optional[str], chat_id: Optional[str]) -> None:
    """§2.7 : alerte (jamais bloquant) si aucun message réel du canal
    Station X depuis 7 jours. Exclut explicitement les raw_messages
    synthétiques du Flux B (`channel = 'trend_strategy'`, voir
    trend_executor.py) — sinon l'activité du Flux B masquerait une
    vraie coupure du canal Telegram. Déduplique la notification à une
    fois par jour via system_state."""
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(received_at) AS last FROM raw_messages WHERE channel != 'trend_strategy'"
        ).fetchone()
    last_at = datetime.fromisoformat(row["last"]) if row and row["last"] else None
    if not is_channel_inactive(last_at, now):
        return

    key = "channel_inactive_last_alert"
    today = now.date().isoformat()
    with connection_scope(db_path) as conn:
        marker = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
        if marker is not None and marker["value"] == today:
            return
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, today, _now_iso()),
        )
    record_trigger(
        db_path, "global", None, None, "channel_inactive", None,
        "Aucun message capté sur le canal Station X depuis 7 jours ou plus",
        bot_token, chat_id,
    )


# ---------------------------------------------------------------------------
# /stop_urgence — suivi "déjà traité" par process (une seule fermeture forcée par activation)
# ---------------------------------------------------------------------------

def get_unhandled_stop_urgence_event_id(db_path: str, process_name: str) -> Optional[int]:
    """Retourne l'id du dernier événement stop_urgence actif si ce
    process ne l'a pas encore traité (fermeture forcée déjà exécutée),
    None sinon — évite de relancer une fermeture forcée à chaque
    itération de boucle tant que /reprendre n'a pas été envoyé."""
    with connection_scope(db_path) as conn:
        event = conn.execute(
            "SELECT id FROM circuit_breaker_events WHERE scope = 'global' AND breaker_type = 'stop_urgence' "
            "AND cleared_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if event is None:
            return None
        marker = conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (f"stop_urgence_handled:{process_name}",)
        ).fetchone()
        if marker is not None and marker["value"] == str(event["id"]):
            return None
        return event["id"]


def mark_stop_urgence_handled(db_path: str, process_name: str, event_id: int) -> None:
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (f"stop_urgence_handled:{process_name}", str(event_id), _now_iso()),
        )
