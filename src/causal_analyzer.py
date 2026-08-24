"""
causal_analyzer.py — Moteur d'analyse causale (§3.11 du CDC, texte
complet relu avant construction, voir docs/DECISIONS.md). MODULE
CRITIQUE (partie calcul/classification), même exigence de couverture
que risk_engine.py — une mauvaise classification pourrait un jour
alimenter le cycle autonome (§3.9, palier séparé) sur une base fausse.

Déclenché AUTOMATIQUEMENT à chaque activation d'un coupe-circuit R
(day_r/week_r/drawdown_r UNIQUEMENT — jamais pause manuelle/
stop_urgence/api_errors/breadth/canal inactif : ces déclencheurs
administratifs ont une cause déjà connue au moment où ils se
produisent, aucune analyse n'apporterait rien). Câblé dans
`circuit_breaker_store.is_asset_blocked`, juste après `record_trigger`
— AUCUNE modification de la décision de blocage elle-même (invariant :
ce module lit et journalise, il ne décide jamais rien côté risque).

Rassemble (§3.11) : tous les trades de la période concernée par le
seuil franchi, contexte de marché (session, spread au moment du
signal), événements macro (`macro_events` — table jamais alimentée à
ce jour, gap documenté, voir docs/DECISIONS.md), score de confiance du
couple (actif, source) via `confidence_scorer.compute_confidence_score`
(réutilisé tel quel, jamais recalculé ici), exposition corrélée entre
trades perdants (chevauchement temporel). "Références externes" (§3.11)
: hors périmètre, aucune source externe intégrée à ce projet — gap
documenté, pas construit.

Produit trois catégories, TOUJOURS déterministes (invariant #1/#9 :
aucun LLM ne classe quoi que ce soit ici) :
- **anomalie_technique** : notifiée IMMÉDIATEMENT (le seuil de volume
  ne s'applique pas, §3.11) — mais ce module ne CORRIGE jamais de code
  lui-même ("corrigée immédiatement" au sens du CDC = intervention
  humaine prioritaire, jamais un auto-correctif silencieux, cohérent
  avec invariant #1 : aucun LLM n'a accès au moteur de risque).
- **evenement_marche** : aucune action, journalisée pour mémoire.
- **hypothese_pattern** : journalisée EN ATTENTE. Ce module ne
  transforme JAMAIS une entrée en proposition — le seuil de volume et
  la promotion en proposition sont la charge du cycle autonome (§3.9,
  palier séparé, voir docs/HYPOTHESES.md), jamais ici. Garde-fou non
  négociable du §3.11, littéral : "une mauvaise journée, même
  parfaitement comprise, ne prouve jamais un pattern."

`analyse_texte` (colonne NOT NULL de `causal_analysis_log`) est
TEMPLATE, jamais généré par un LLM — écart assumé par rapport au
patron `trade_analyzer.py` (narratif LLM sur des faits déjà
déterministes) : ce log est un enregistrement d'audit/conformité, pas
un contenu pédagogique pour un humain qui découvre le trade — la
reproductibilité et l'absence de coût/latence API à chaque
déclenchement de coupe-circuit priment ici sur la lisibilité en prose.
Voir docs/DECISIONS.md pour la discussion complète.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from src import confidence_scorer
from src.audit_notifier import send_notification
from src.db import connection_scope

logger = logging.getLogger(__name__)

CATEGORY_ANOMALIE_TECHNIQUE = "anomalie_technique"
CATEGORY_EVENEMENT_MARCHE = "evenement_marche"
CATEGORY_HYPOTHESE_PATTERN = "hypothese_pattern"

# Constantes a priori (invariant #10), jamais ajustées sur un résultat :
# - slippage anormal : au-delà de ce multiple du spread observé au
#   signal, considéré hors norme (§3.11 "slippage hors norme").
# - actifs corrélés : nombre d'AUTRES actifs ayant déclenché un
#   coupe-circuit R le même jour civil UTC pour classer "événement de
#   marché" — volontairement plus sensible que
#   circuit_breaker.BREADTH_PAUSE_THRESHOLD_ASSETS (5, qui déclenche sa
#   propre pause générale) : ce module classe une observation, il ne
#   bloque rien, le seuil peut être plus bas sans conséquence de risque.
SLIPPAGE_ANOMALY_SPREAD_MULTIPLIER = 5.0
CORRELATED_MARKET_EVENT_MIN_OTHER_ASSETS = 2


@dataclass(frozen=True)
class TradeWindowEntry:
    trade_id: int
    actif: str
    source: str
    direction: str
    r_multiple_total: Optional[float]
    ouvert_at: str
    ferme_at: Optional[str]
    slippage_entree: Optional[float]
    spread_at_signal: Optional[float]


@dataclass(frozen=True)
class CausalContext:
    trades: List[TradeWindowEntry] = field(default_factory=list)
    other_assets_triggered_same_day: int = 0
    macro_events_in_window: List[dict] = field(default_factory=list)
    confidence_nb_trades: int = 0
    confidence_esperance_r: Optional[float] = None
    correlated_exposure_pairs: List[Tuple[int, int]] = field(default_factory=list)
    has_api_error_anomaly: bool = False  # série d'erreurs API (circuit_breaker_events "api_errors") dans la fenêtre


@dataclass(frozen=True)
class CausalAnalysis:
    categorie: str
    trades_concernes_ids: List[int]
    contexte_json: str
    analyse_texte: str


# ---------------------------------------------------------------------------
# Fenêtre temporelle par type de coupe-circuit — même sémantique jour/semaine
# UTC que circuit_breaker.compute_r_stats (pas une nouvelle convention).
# ---------------------------------------------------------------------------

def window_start(breaker_type: str, now: datetime) -> Optional[datetime]:
    """None = depuis toujours (drawdown_r : "depuis le plus haut", pas
    une fenêtre calendaire — voir circuit_breaker.py, aucune borne
    calendaire fixe pour ce cas)."""
    if now.tzinfo is None:
        raise ValueError("`now` doit être timezone-aware (UTC)")
    now_utc = now.astimezone(timezone.utc)
    if breaker_type == "day_r":
        return now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if breaker_type == "week_r":
        return (now_utc - timedelta(days=now_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if breaker_type == "drawdown_r":
        return None
    raise ValueError(f"breaker_type inconnu pour une fenêtre causale : {breaker_type!r}")


# ---------------------------------------------------------------------------
# Calcul pur (100% couvert) — aucune I/O
# ---------------------------------------------------------------------------

def compute_correlated_exposure(trades: List[TradeWindowEntry]) -> List[Tuple[int, int]]:
    """Paires de trades PERDANTS dont les fenêtres [ouvert_at, ferme_at]
    se chevauchent — exposition corrélée (§3.11). Un trade encore ouvert
    (ferme_at=None) est exclu (pas encore de R-multiple à corréler).
    O(n²), volumes toujours petits ici (une fenêtre de coupe-circuit,
    jamais tout l'historique)."""
    losing = [
        t for t in trades
        if t.r_multiple_total is not None and t.r_multiple_total < 0 and t.ferme_at is not None
    ]
    pairs: List[Tuple[int, int]] = []
    for i in range(len(losing)):
        for j in range(i + 1, len(losing)):
            a, b = losing[i], losing[j]
            a_open, a_close = datetime.fromisoformat(a.ouvert_at), datetime.fromisoformat(a.ferme_at)
            b_open, b_close = datetime.fromisoformat(b.ouvert_at), datetime.fromisoformat(b.ferme_at)
            if a_open < b_close and b_open < a_close:
                pairs.append((a.trade_id, b.trade_id))
    return pairs


def _has_slippage_anomaly(trades: List[TradeWindowEntry]) -> bool:
    for t in trades:
        if t.slippage_entree is None or t.spread_at_signal is None or t.spread_at_signal <= 0:
            continue
        if abs(t.slippage_entree) > SLIPPAGE_ANOMALY_SPREAD_MULTIPLIER * t.spread_at_signal:
            return True
    return False


def classify_category(
    has_api_error_anomaly: bool, has_slippage_anomaly: bool,
    other_assets_triggered_same_day: int, macro_events_in_window: List[dict],
) -> str:
    """Classification déterministe, TOUJOURS dans cet ordre de priorité
    (§3.11, catégories mutuellement exclusives) :
    1. Anomalie technique — la seule catégorie qui court-circuite le
       seuil de volume, doit être détectée en premier.
    2. Événement de marché généralisé — plusieurs actifs touchés le même
       jour, ou une annonce macro fort dans la fenêtre.
    3. Hypothèse de pattern — résiduelle, jamais un choix par défaut
       silencieux : c'est ce qui reste après avoir écarté les deux
       premières causes connues."""
    if has_api_error_anomaly or has_slippage_anomaly:
        return CATEGORY_ANOMALIE_TECHNIQUE
    if other_assets_triggered_same_day >= CORRELATED_MARKET_EVENT_MIN_OTHER_ASSETS:
        return CATEGORY_EVENEMENT_MARCHE
    if any(e.get("impact") == "fort" for e in macro_events_in_window):
        return CATEGORY_EVENEMENT_MARCHE
    return CATEGORY_HYPOTHESE_PATTERN


def build_analyse_texte(
    categorie: str, asset: str, source: str, breaker_type: str, context: CausalContext,
    has_api_error_anomaly: bool, has_slippage_anomaly: bool,
) -> str:
    """Texte déterministe (jamais un LLM, voir docstring du module) —
    un gabarit par catégorie, toujours les mêmes faits, aucune
    interprétation ajoutée au-delà de ce que classify_category a déjà
    décidé."""
    losing_count = sum(1 for t in context.trades if t.r_multiple_total is not None and t.r_multiple_total < 0)
    lines = [
        f"Coupe-circuit {breaker_type} déclenché sur {asset} ({source}).",
        f"{len(context.trades)} trade(s) dans la fenêtre analysée, dont {losing_count} perdant(s).",
    ]
    if categorie == CATEGORY_ANOMALIE_TECHNIQUE:
        if has_api_error_anomaly:
            lines.append("Anomalie technique : série d'erreurs API détectée dans la fenêtre.")
        if has_slippage_anomaly:
            lines.append(
                f"Anomalie technique : slippage à l'entrée dépassant {SLIPPAGE_ANOMALY_SPREAD_MULTIPLIER:.0f}x "
                "le spread observé sur au moins un trade de la fenêtre."
            )
        lines.append("Catégorie : anomalie_technique — seuil de volume non applicable, intervention prioritaire.")
    elif categorie == CATEGORY_EVENEMENT_MARCHE:
        if context.other_assets_triggered_same_day >= CORRELATED_MARKET_EVENT_MIN_OTHER_ASSETS:
            lines.append(f"{context.other_assets_triggered_same_day} autre(s) actif(s) déclenché(s) le même jour civil UTC.")
        if any(e.get("impact") == "fort" for e in context.macro_events_in_window):
            lines.append("Événement macro à impact fort recensé dans la fenêtre.")
        lines.append("Catégorie : evenement_marche — aucune action, comportement système normal face au marché.")
    else:
        lines.append(
            f"Aucune anomalie technique ni événement de marché généralisé identifié — "
            f"score de confiance actuel : {context.confidence_nb_trades} trade(s), "
            f"espérance nette {context.confidence_esperance_r if context.confidence_esperance_r is not None else 'indisponible'}."
        )
        if context.correlated_exposure_pairs:
            lines.append(f"{len(context.correlated_exposure_pairs)} paire(s) de trades perdants à exposition corrélée (chevauchement temporel).")
        lines.append(
            "Catégorie : hypothese_pattern — journalisée en attente, ne devient jamais une proposition ici "
            "(seuil de volume et promotion : cycle autonome séparé, §3.9)."
        )
    return " ".join(lines)


def compute_causal_analysis(
    asset: str, source: str, breaker_type: str, context: CausalContext,
) -> CausalAnalysis:
    """Assemble classification + texte + contexte JSON. Pure (aucune
    I/O) — `context` est déjà entièrement rassemblé par l'appelant I/O
    (`record_causal_analysis`)."""
    has_slippage_anomaly = _has_slippage_anomaly(context.trades)
    categorie = classify_category(
        has_api_error_anomaly=context.has_api_error_anomaly,
        has_slippage_anomaly=has_slippage_anomaly,
        other_assets_triggered_same_day=context.other_assets_triggered_same_day,
        macro_events_in_window=context.macro_events_in_window,
    )
    analyse_texte = build_analyse_texte(
        categorie, asset, source, breaker_type, context,
        has_api_error_anomaly=context.has_api_error_anomaly, has_slippage_anomaly=has_slippage_anomaly,
    )
    contexte_dict = {
        "other_assets_triggered_same_day": context.other_assets_triggered_same_day,
        "macro_events_in_window": context.macro_events_in_window,
        "has_api_error_anomaly": context.has_api_error_anomaly,
        "has_slippage_anomaly": has_slippage_anomaly,
        "confidence_nb_trades": context.confidence_nb_trades,
        "confidence_esperance_r": context.confidence_esperance_r,
        "correlated_exposure_pairs": context.correlated_exposure_pairs,
        "trades": [
            {
                "trade_id": t.trade_id, "actif": t.actif, "source": t.source, "direction": t.direction,
                "r_multiple_total": t.r_multiple_total, "ouvert_at": t.ouvert_at, "ferme_at": t.ferme_at,
            }
            for t in context.trades
        ],
    }
    return CausalAnalysis(
        categorie=categorie,
        trades_concernes_ids=[t.trade_id for t in context.trades],
        contexte_json=json.dumps(contexte_dict, ensure_ascii=False),
        analyse_texte=analyse_texte,
    )


# ---------------------------------------------------------------------------
# Orchestration I/O — lecture seule sur trades/circuit_breaker_events/
# macro_events, appelle confidence_scorer.compute_confidence_score
# (existant, jamais modifié) ; écrit UNIQUEMENT dans causal_analysis_log.
# ---------------------------------------------------------------------------

def _fetch_trades_in_window(db_path: str, asset: str, source: str, start: Optional[datetime]) -> List[TradeWindowEntry]:
    query = (
        "SELECT t.id AS id, t.actif AS actif, t.source AS source, t.direction AS direction, "
        "t.r_multiple_total AS r_multiple_total, t.ouvert_at AS ouvert_at, t.ferme_at AS ferme_at, "
        "t.slippage_entree AS slippage_entree, ms.spread AS spread_at_signal "
        "FROM trades t LEFT JOIN market_snapshots ms ON ms.signal_id = t.signal_id "
        "WHERE t.actif = ? AND t.source = ? AND t.statut = 'ferme'"
    )
    params: list = [asset, source]
    if start is not None:
        query += " AND t.ouvert_at >= ?"
        params.append(start.isoformat())
    with connection_scope(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        TradeWindowEntry(
            trade_id=row["id"], actif=row["actif"], source=row["source"], direction=row["direction"],
            r_multiple_total=row["r_multiple_total"], ouvert_at=row["ouvert_at"], ferme_at=row["ferme_at"],
            slippage_entree=row["slippage_entree"], spread_at_signal=row["spread_at_signal"],
        )
        for row in rows
    ]


def _count_other_assets_triggered_same_day(db_path: str, db_asset: str, day_iso: str) -> int:
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT actif) AS n FROM circuit_breaker_events "
            "WHERE scope = 'asset' AND actif IS NOT NULL AND actif != ? "
            "AND breaker_type IN ('day_r', 'week_r', 'drawdown_r') AND date(triggered_at) = date(?)",
            (db_asset, day_iso),
        ).fetchone()
    return row["n"] if row is not None else 0


def _fetch_macro_events_in_window(db_path: str, start: Optional[datetime], now: datetime) -> List[dict]:
    query = "SELECT datetime, devise, intitule, impact FROM macro_events WHERE datetime <= ?"
    params: list = [now.isoformat()]
    if start is not None:
        query = "SELECT datetime, devise, intitule, impact FROM macro_events WHERE datetime BETWEEN ? AND ?"
        params = [start.isoformat(), now.isoformat()]
    with connection_scope(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _has_recent_api_error_trigger(db_path: str, start: Optional[datetime]) -> bool:
    query = "SELECT COUNT(*) AS n FROM circuit_breaker_events WHERE breaker_type = 'api_errors'"
    params: list = []
    if start is not None:
        query += " AND triggered_at >= ?"
        params.append(start.isoformat())
    with connection_scope(db_path) as conn:
        row = conn.execute(query, params).fetchone()
    return (row["n"] if row is not None else 0) > 0


def gather_causal_context(db_path: str, asset: str, source: str, breaker_type: str, now: datetime) -> CausalContext:
    start = window_start(breaker_type, now)
    trades = _fetch_trades_in_window(db_path, asset, source, start)
    score = confidence_scorer.compute_confidence_score(db_path, asset, source)
    return CausalContext(
        trades=trades,
        other_assets_triggered_same_day=_count_other_assets_triggered_same_day(db_path, asset, now.isoformat()),
        macro_events_in_window=_fetch_macro_events_in_window(db_path, start, now),
        confidence_nb_trades=score.nb_trades,
        confidence_esperance_r=score.esperance_r,
        correlated_exposure_pairs=compute_correlated_exposure(trades),
        has_api_error_anomaly=_has_recent_api_error_trigger(db_path, start),
    )


def record_causal_analysis(
    db_path: str, asset: str, source: str, breaker_type: str, now: datetime,
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
) -> int:
    """Point d'entrée appelé par `circuit_breaker_store.is_asset_blocked`
    à chaque nouveau déclenchement day_r/week_r/drawdown_r. Rassemble le
    contexte (lecture seule), classe, journalise dans
    `causal_analysis_log`, notifie IMMÉDIATEMENT si anomalie_technique
    (§3.11 : le seuil de volume ne s'applique pas à cette catégorie).
    Ne lève jamais d'exception : une erreur ici ne doit jamais empêcher
    le coupe-circuit lui-même de fonctionner (fail-safe, invariant #7 —
    ce module est un observateur, jamais un point de défaillance pour
    la décision de risque)."""
    try:
        context = gather_causal_context(db_path, asset, source, breaker_type, now)
        analysis = compute_causal_analysis(asset, source, breaker_type, context)
        now_iso = now.isoformat()
        with connection_scope(db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO causal_analysis_log (declencheur, trades_concernes_ids, contexte_json, categorie, analyse_texte, action_prise, created_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (
                    f"{breaker_type}:{asset}:{source}", json.dumps(analysis.trades_concernes_ids),
                    analysis.contexte_json, analysis.categorie, analysis.analyse_texte, now_iso,
                ),
            )
            log_id = cursor.lastrowid

        if analysis.categorie == CATEGORY_ANOMALIE_TECHNIQUE and bot_token and chat_id:
            send_notification(
                bot_token, chat_id,
                f"⚠️ Analyse causale — anomalie technique détectée ({asset}, {source})\n{analysis.analyse_texte}",
            )
        return log_id
    except Exception:
        logger.exception(
            "Échec de l'analyse causale (asset=%s source=%s breaker_type=%s) — le coupe-circuit reste inchangé",
            asset, source, breaker_type,
        )
        return -1
