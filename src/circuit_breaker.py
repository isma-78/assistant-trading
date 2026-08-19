"""
circuit_breaker.py — Coupe-circuits par actif (§2.7 du CDC) et plafond
d'exposition simultanée (§2.3). MODULE CRITIQUE, 100% de couverture
exigée (même règle que risk_engine.py, invariant #2) : un bug ici laisse
courir une perte que le CDC voulait explicitement contenir.

Logique pure, sans I/O (invariant #1 : aucun LLM, aucun accès broker
ici) — la lecture de l'historique de trades, la persistance des
déclenchements et les notifications sont dans circuit_breaker_store.py,
même séparation que risk_engine.py / envelope_store.py.

Seuils fixés par le CDC (§2.7), jamais paramétrables à chaud (invariant #6) :
    -2R cumulés dans la journée (UTC)  -> arrêt entrées sur l'actif, auto le lendemain
    -5R cumulés dans la semaine (UTC)  -> arrêt complet sur l'actif, reprise manuelle
    -12R depuis le plus haut           -> arrêt + alerte, reprise manuelle
    Exposition simultanée > 10% de l'enveloppe -> refus de la nouvelle entrée (§2.3)
    3 erreurs API consécutives         -> pause générale (entrées uniquement, voir docs/DECISIONS.md)
    Coupe-circuit sur >= 5 actifs      -> pause générale (bug probable, pas marché)
    Aucun message capté depuis 7 jours -> alerte (jamais bloquant)

"Depuis le plus haut" (§2.7) : plus haut du cumul de R-multiples réalisés
sur cet actif/source depuis son tout premier trade clos, jamais
réinitialisé par un rechargement d'enveloppe — c'est une mesure de la
performance du système sur cet actif, pas de son solde.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

DAY_LOSS_THRESHOLD_R = -2.0
WEEK_LOSS_THRESHOLD_R = -5.0
DRAWDOWN_FROM_PEAK_THRESHOLD_R = -12.0
EXPOSURE_CAP_FRACTION = 0.10
API_ERROR_STREAK_THRESHOLD = 3
BREADTH_PAUSE_THRESHOLD_ASSETS = 5
CHANNEL_INACTIVITY_THRESHOLD_DAYS = 7


@dataclass(frozen=True)
class RStats:
    day_r: float
    week_r: float
    drawdown_from_peak_r: float
    cumulative_r: float
    peak_r: float


def compute_r_stats(closed_trades: List[Tuple[str, float]], now: datetime) -> RStats:
    """`closed_trades` : liste de (ferme_at_iso, r_multiple), dans un
    ordre quelconque (retriée ici par date). Jour et semaine calculés en
    UTC (le CDC ne fixe pas de fuseau — choix documenté dans
    docs/DECISIONS.md), semaine = lundi 00:00 UTC -> dimanche 23:59:59.

    Lève ValueError sur une entrée incohérente (timestamp non
    timezone-aware, `now` non timezone-aware) plutôt que de calculer un
    résultat trompeur — l'appelant (evaluate_circuit_breakers) est
    responsable de traiter ça comme un fail-safe bloquant, jamais comme
    un R silencieux."""
    if now.tzinfo is None:
        raise ValueError("`now` doit être timezone-aware (UTC)")

    parsed = []
    for ts, r in closed_trades:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            raise ValueError(f"Horodatage non timezone-aware dans l'historique de trades : {ts!r}")
        parsed.append((dt.astimezone(timezone.utc), r))
    parsed.sort(key=lambda pair: pair[0])

    now_utc = now.astimezone(timezone.utc)
    today = now_utc.date()
    week_start = (now_utc - timedelta(days=now_utc.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    day_r = sum(r for ts, r in parsed if ts.date() == today)
    week_r = sum(r for ts, r in parsed if ts >= week_start)

    cumulative = 0.0
    peak = 0.0
    for _, r in parsed:
        cumulative += r
        peak = max(peak, cumulative)

    return RStats(
        day_r=round(day_r, 6),
        week_r=round(week_r, 6),
        drawdown_from_peak_r=round(cumulative - peak, 6),
        cumulative_r=round(cumulative, 6),
        peak_r=round(peak, 6),
    )


@dataclass(frozen=True)
class AssetBreakerStatus:
    blocked: bool
    active_reasons: List[str]   # breaker_type actifs, ex: ["day_r", "week_r"]
    new_triggers: List[str]     # sous-ensemble d'active_reasons qui vient de se déclencher (à journaliser/notifier)
    r_stats: RStats


def _evaluate_asset_breakers(
    r_stats: RStats,
    day_already_triggered_today: bool,
    week_latched: bool,
    drawdown_latched: bool,
) -> AssetBreakerStatus:
    active: List[str] = []
    new: List[str] = []

    if day_already_triggered_today:
        active.append("day_r")
    elif r_stats.day_r <= DAY_LOSS_THRESHOLD_R:
        active.append("day_r")
        new.append("day_r")

    if week_latched:
        active.append("week_r")
    elif r_stats.week_r <= WEEK_LOSS_THRESHOLD_R:
        active.append("week_r")
        new.append("week_r")

    if drawdown_latched:
        active.append("drawdown_r")
    elif r_stats.drawdown_from_peak_r <= DRAWDOWN_FROM_PEAK_THRESHOLD_R:
        active.append("drawdown_r")
        new.append("drawdown_r")

    return AssetBreakerStatus(blocked=bool(active), active_reasons=active, new_triggers=new, r_stats=r_stats)


def evaluate_circuit_breakers(
    closed_trades: List[Tuple[str, float]],
    now: datetime,
    day_already_triggered_today: bool,
    week_latched: bool,
    drawdown_latched: bool,
) -> AssetBreakerStatus:
    """Point d'entrée unique de décision des coupe-circuits R par actif
    (invariant #3-like : jamais contourné). Ne lève jamais d'exception :
    toute erreur interne bloque l'actif plutôt que de laisser passer une
    entrée sur un calcul de risque cumulé indéterminé (fail-safe,
    invariant #7 — même philosophie que risk_engine.evaluate_new_entry)."""
    try:
        r_stats = compute_r_stats(closed_trades, now)
        return _evaluate_asset_breakers(r_stats, day_already_triggered_today, week_latched, drawdown_latched)
    except Exception:
        return AssetBreakerStatus(
            blocked=True,
            active_reasons=["internal_error"],
            new_triggers=[],
            r_stats=RStats(0.0, 0.0, 0.0, 0.0, 0.0),
        )


def evaluate_exposure_cap(open_risk_eur: float, envelope_balance: float, new_risk_eur: float) -> bool:
    """True si l'ajout de `new_risk_eur` dépasserait le plafond de 10% de
    l'enveloppe engagée simultanément (§2.3) — la nouvelle entrée doit
    alors être refusée. `envelope_balance` <= 0 -> toujours dépassé
    (ceinture et bretelles : risk_engine rejette déjà ce cas via
    ENVELOPE_DEPLETED, ce module ne doit jamais diverger)."""
    if envelope_balance <= 0:
        return True
    return (open_risk_eur + new_risk_eur) > envelope_balance * EXPOSURE_CAP_FRACTION


def evaluate_api_error_streak(consecutive_errors: int, threshold: int = API_ERROR_STREAK_THRESHOLD) -> bool:
    """§2.7, surcouche anomalie système : 3 erreurs API consécutives ->
    pause générale des entrées."""
    return consecutive_errors >= threshold


def evaluate_breadth_pause(distinct_blocked_assets: int, threshold: int = BREADTH_PAUSE_THRESHOLD_ASSETS) -> bool:
    """§2.7 : coupe-circuit déclenché sur >= 5 actifs simultanément ->
    pause générale (bug probable, pas marché)."""
    return distinct_blocked_assets >= threshold


def is_channel_inactive(
    last_message_at: Optional[datetime], now: datetime, days: int = CHANNEL_INACTIVITY_THRESHOLD_DAYS
) -> bool:
    """§2.7 : aucun message capté depuis 7 jours -> alerte (jamais
    bloquant, contrairement aux autres coupe-circuits — voir appelant).
    `last_message_at=None` (aucun message jamais capté) n'est pas traité
    comme une inactivité ici : c'est un état de démarrage à distinguer
    d'un silence après activité, hors périmètre de cette fonction."""
    if last_message_at is None:
        return False
    if now.tzinfo is None or last_message_at.tzinfo is None:
        raise ValueError("`now` et `last_message_at` doivent être timezone-aware (UTC)")
    return (now.astimezone(timezone.utc) - last_message_at.astimezone(timezone.utc)) >= timedelta(days=days)
