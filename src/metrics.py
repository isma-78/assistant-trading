"""
metrics.py — Agrégation de métriques par actif et par source (§4.4, §4.5) :
R-multiple, espérance, profit factor, drawdown, taux de réussite
indicatif. Purement lecture/calcul, aucune décision ici — mais future
base du score de confiance (§2.4) et de la bascule 2%/4% (§2.3),
justifiant une couverture 100% sur la partie calcul (demande explicite
d'Ismaël, 20/08/2026), même régime que risk_engine.py.

Calculé À LA DEMANDE, pas de snapshot périodique dans `metrics_snapshot`
(§4.5) malgré son existence dans le schéma : rien d'autre ne lit cette
table aujourd'hui, y ajouter un scheduler serait de la complexité sans
consommateur — écart documenté dans docs/DECISIONS.md, réversible si un
suivi de tendance dans le temps devient utile.

Source de vérité : `trades.r_multiple_total` (jamais recalculé depuis
`trade_analysis`, qui ne fait que le recopier — voir trade_analyzer.py)
pour les métriques en R, `envelope_ledger` pour les gains/pertes en euros
réellement crédités/débités aux enveloppes (donc déjà après la règle des
50%, §2.3 — pas le PnL brut du trade).

Trades clos par `/stop_urgence` (`trades.cloture_reason = 'stop_urgence'`,
ajouté le 20/08/2026) EXCLUS des statistiques en R (espérance, profit
factor, taux de réussite, drawdown) — voir docs/DECISIONS.md pour le
raisonnement complet : un arrêt d'urgence sort au prix du marché à un
instant arbitraire (déclenché manuellement, ou par une anomalie système),
ça ne mesure jamais si le placement du stop/TP/trailing de la stratégie
est bien calibré, contrairement à une sortie organique. Le P&L en euros
(`envelope_ledger`), lui, N'EST PAS filtré : l'argent a réellement bougé
sur l'enveloppe, peu importe pourquoi le trade s'est fermé — filtrer
l'un et pas l'autre est un choix explicite, pas un oubli. `get_closed_
trades_r()` (circuit_breaker_store.py) N'EST PAS ce module : il alimente
le coupe-circuit §2.7, qui doit rester informé même d'une perte issue
d'un arrêt d'urgence (invariant #7, fail-safe) — jamais réutilisé ici.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from src.db import connection_scope

# Même normalisation que circuit_breaker_store._normalize_source /
# executor._envelope_source_key / confidence_scorer._normalize_source —
# dupliquée plutôt qu'importée (nom privé ailleurs), cohérent avec le
# choix déjà fait dans ces modules. Voir docs/DECISIONS.md.
#
# Généralisée le 21/08/2026 (voir docs/DECISIONS.md) : ne reconnaissait
# QUE "hypothesis" (H1) — toute autre source retombait silencieusement
# sur "stationx", mélangeant ses métriques à celles de Station X. Toute
# nouvelle hypothèse DOIT être ajoutée à _KNOWN_HYPOTHESIS_SOURCES ici,
# sinon ses trades seront comptés dans les métriques de Station X.
HYPOTHESIS_SOURCE = "hypothesis"    # Hypothèse #1
HYPOTHESIS3_SOURCE = "hypothesis3"  # Hypothèse #3
HYPOTHESIS2_SOURCE = "hypothesis2"  # Hypothèse #2
HYPOTHESIS4_SOURCE = "hypothesis4"  # Hypothèse #4 (21/08/2026 — validée en démo, non déployée)
_KNOWN_HYPOTHESIS_SOURCES = {HYPOTHESIS_SOURCE, HYPOTHESIS3_SOURCE, HYPOTHESIS2_SOURCE, HYPOTHESIS4_SOURCE}


def _normalize_source(source: str) -> str:
    return source if source in _KNOWN_HYPOTHESIS_SOURCES else "stationx"


# ---------------------------------------------------------------------------
# Calcul pur (100% couvert)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssetMetrics:
    actif: str
    source: str
    nb_trades: int
    esperance_r: Optional[float]
    profit_factor: Optional[float]          # None si indéterminé (aucun gain ET aucune perte) ; float("inf") si aucune perte
    taux_reussite_indicatif: Optional[float]  # pourcentage [0, 100], None si nb_trades == 0
    drawdown_courant_r: float
    drawdown_max_r: float


def compute_asset_metrics(actif: str, source: str, r_multiples: List[float]) -> AssetMetrics:
    """`r_multiples` : R-multiple de chaque trade fermé pour (actif,
    source), dans l'ordre chronologique (la chronologie ne compte que
    pour le drawdown — espérance/PF/taux de réussite sont insensibles à
    l'ordre)."""
    nb_trades = len(r_multiples)
    if nb_trades == 0:
        return AssetMetrics(actif, source, 0, None, None, None, 0.0, 0.0)

    esperance_r = round(sum(r_multiples) / nb_trades, 4)

    gains = sum(r for r in r_multiples if r > 0)
    pertes = sum(r for r in r_multiples if r < 0)  # <= 0
    if pertes < 0:
        profit_factor: Optional[float] = round(gains / abs(pertes), 4)
    elif gains > 0:
        profit_factor = float("inf")  # que des gains, aucune perte
    else:
        profit_factor = None  # aucun gain ni perte (tous les trades à R=0 pile) : indéterminé

    nb_gagnants = sum(1 for r in r_multiples if r > 0)
    taux_reussite = round(100 * nb_gagnants / nb_trades, 2)

    drawdown_courant, drawdown_max = _compute_drawdowns(r_multiples)

    return AssetMetrics(
        actif=actif, source=source, nb_trades=nb_trades,
        esperance_r=esperance_r, profit_factor=profit_factor,
        taux_reussite_indicatif=taux_reussite,
        drawdown_courant_r=drawdown_courant, drawdown_max_r=drawdown_max,
    )


def _compute_drawdowns(r_multiples: List[float]) -> Tuple[float, float]:
    """(drawdown_courant, drawdown_max) sur la courbe cumulative de R —
    valeurs négatives ou nulles, jamais positives. `drawdown_courant` :
    écart entre le cumul actuel et le plus haut déjà atteint (même
    définition que circuit_breaker.compute_r_stats.drawdown_from_peak_r,
    non réimportée ici pour rester indépendant d'un module de décision
    de risque — celui-ci est un module de reporting). `drawdown_max` :
    le PIRE creux jamais atteint historiquement, pas seulement l'écart
    au pic actuel (peut avoir été pire par le passé puis s'être résorbé)."""
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for r in r_multiples:
        cumulative += r
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    current = round(cumulative - peak, 6)
    return current, round(worst, 6)


def compute_period_pnl(movements: List[Tuple[str, float]], now: datetime) -> Tuple[float, float, float]:
    """(pnl_semaine, pnl_mois, pnl_depuis_le_debut) en euros.
    `movements` : liste de (recorded_at_iso, montant) — peu importe
    l'ordre. Semaine = lundi 00:00 UTC (même définition que
    circuit_breaker.py, cohérence dans tout le projet) ; mois = 1er du
    mois 00:00 UTC.

    Lève ValueError sur un horodatage non timezone-aware plutôt que de
    calculer un total silencieusement faux — module de reporting, pas de
    décision de risque : pas de fail-safe qui masquerait l'erreur, elle
    doit remonter à l'appelant (dashboard.py décide comment l'afficher)."""
    if now.tzinfo is None:
        raise ValueError("`now` doit être timezone-aware (UTC)")
    now_utc = now.astimezone(timezone.utc)
    week_start = (now_utc - timedelta(days=now_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    week_total = month_total = all_time_total = 0.0
    for ts, amount in movements:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            raise ValueError(f"Horodatage non timezone-aware dans l'historique de mouvements : {ts!r}")
        dt = dt.astimezone(timezone.utc)
        all_time_total += amount
        if dt >= month_start:
            month_total += amount
        if dt >= week_start:
            week_total += amount
    return round(week_total, 2), round(month_total, 2), round(all_time_total, 2)


# ---------------------------------------------------------------------------
# Orchestration I/O — lecture DB, aucun accès broker (invariant : le
# dashboard ne doit dépendre d'aucune session Capital.com pour exister)
# ---------------------------------------------------------------------------

def get_all_asset_keys(db_path: str) -> List[Tuple[str, str]]:
    """(actif, source) distincts ayant au moins une enveloppe créée —
    reflète les actifs réellement initialisés en pratique, pas la liste
    blanche statique (asset_whitelist.py nécessite un CapitalClient pour
    les taux de change EUR, hors périmètre d'un module de reporting)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT actif, source FROM envelopes ORDER BY actif, source").fetchall()
    return [(row["actif"], row["source"]) for row in rows]


def get_closed_trades_r_for_stats(db_path: str, actif: str, source: str) -> List[Tuple[str, float]]:
    """Comme circuit_breaker_store.get_closed_trades_r, mais EXCLUT les
    trades clos par /stop_urgence (voir docstring du module) — jamais
    réutilisée par le coupe-circuit, qui a besoin de connaître TOUTE
    perte réalisée, y compris un arrêt d'urgence."""
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT ferme_at, r_multiple_total, source FROM trades "
            "WHERE actif = ? AND statut = 'ferme' AND r_multiple_total IS NOT NULL "
            "AND (cloture_reason IS NULL OR cloture_reason != 'stop_urgence')",
            (actif,),
        ).fetchall()
    return [(row["ferme_at"], row["r_multiple_total"]) for row in rows if _normalize_source(row["source"]) == normalized]


def get_asset_metrics(db_path: str, actif: str, source: str) -> AssetMetrics:
    trades_r = get_closed_trades_r_for_stats(db_path, actif, source)
    trades_r.sort(key=lambda pair: pair[0])  # chronologique, requis pour le drawdown
    r_multiples = [r for _, r in trades_r]
    return compute_asset_metrics(actif, _normalize_source(source), r_multiples)


def get_all_asset_metrics(db_path: str) -> List[AssetMetrics]:
    return [get_asset_metrics(db_path, actif, source) for actif, source in get_all_asset_keys(db_path)]


def get_trade_pnl_movements(db_path: str, actif: str, source: str) -> List[Tuple[str, float]]:
    """Mouvements `trade_pnl` de `envelope_ledger` pour (actif, source) —
    déjà après la règle des 50% (§2.3), pas le PnL brut du trade."""
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT el.recorded_at AS recorded_at, (el.montant_apres - el.montant_avant) AS amount, e.source AS source "
            "FROM envelope_ledger el JOIN envelopes e ON e.id = el.envelope_id "
            "WHERE e.actif = ? AND el.type_mouvement = 'trade_pnl'",
            (actif,),
        ).fetchall()
    return [(row["recorded_at"], row["amount"]) for row in rows if _normalize_source(row["source"]) == normalized]


def get_asset_period_pnl(db_path: str, actif: str, source: str, now: datetime) -> Tuple[float, float, float]:
    return compute_period_pnl(get_trade_pnl_movements(db_path, actif, source), now)


def get_aggregate_period_pnl(db_path: str, now: datetime) -> Tuple[float, float, float]:
    """Gains/pertes agrégés, tous actifs/sources confondus."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT recorded_at, (montant_apres - montant_avant) AS amount "
            "FROM envelope_ledger WHERE type_mouvement = 'trade_pnl'"
        ).fetchall()
    return compute_period_pnl([(row["recorded_at"], row["amount"]) for row in rows], now)


def get_reserve_total(db_path: str) -> float:
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT reserve_totale FROM reserve_ledger ORDER BY id DESC LIMIT 1").fetchone()
    return row["reserve_totale"] if row is not None else 0.0


def get_total_demo_capital(db_path: str) -> float:
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT COALESCE(SUM(capital_courant), 0) AS total FROM envelopes WHERE mode = 'demo'").fetchone()
    return round(row["total"], 2)


def get_trade_counts_by_period(db_path: str, now: datetime) -> dict:
    """Nombre de trades clôturés par période (semaine/mois/depuis le
    début), par actif et par source — bloc "Trades" du §4.6."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT actif, source, ferme_at FROM trades WHERE statut = 'ferme' AND ferme_at IS NOT NULL"
        ).fetchall()

    if now.tzinfo is None:
        raise ValueError("`now` doit être timezone-aware (UTC)")
    now_utc = now.astimezone(timezone.utc)
    week_start = (now_utc - timedelta(days=now_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    counts: dict = {}
    for row in rows:
        key = (row["actif"], _normalize_source(row["source"]))
        entry = counts.setdefault(key, {"semaine": 0, "mois": 0, "total": 0})
        entry["total"] += 1
        dt = datetime.fromisoformat(row["ferme_at"]).astimezone(timezone.utc)
        if dt >= month_start:
            entry["mois"] += 1
        if dt >= week_start:
            entry["semaine"] += 1
    return counts
