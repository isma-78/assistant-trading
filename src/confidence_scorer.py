"""
confidence_scorer.py — Score de confiance statistique par actif et par
source (§2.4 du CDC), en **mode observation uniquement** (demande
explicite d'Ismaël, 20/08/2026) : aucune décision réelle n'en dépend
aujourd'hui (§2.5 allocator et le verrou §4.9 restent non construits,
volontairement — inutile de construire une décision qui n'a rien à
décider avec 0 à quelques dizaines de trades par (actif, source)).

Calculé À LA DEMANDE, jamais persisté automatiquement dans
`confidence_scores` (table du §4.5, existe dans le schéma) : même choix
et même raisonnement que `metrics.py` pour `metrics_snapshot` — aucun
consommateur n'existe encore pour un historique de scores (allocator,
hypothesis_engine), écrire une ligne à chaque `/dashboard` serait de la
complexité sans lecteur. Réversible dès qu'un besoin réel apparaît. Voir
docs/DECISIONS.md.

Formule exacte du §2.4 :

    Conditions éliminatoires (toutes obligatoires) :
        - nb_trades >= 20 (phase A) puis >= 50 (phase B)
        - espérance nette > 0
        - taille minimale broker compatible avec l'enveloppe
        - spread médian < 15% du stop typique de l'actif

    score = espérance_nette_R
          × facteur_échantillon   (√(nb_trades/50), plafonné à 1)
          × facteur_stabilité     (1 − drawdown_max/20%, plancher 0)

Deux écarts assumés au texte littéral, documentés en détail dans
docs/DECISIONS.md (20/08/2026) plutôt que masqués :

1. **Unité de `drawdown_max`** : le CDC écrit "drawdown_max/20%" sans
   préciser l'unité. Le seul drawdown calculé partout ailleurs dans le
   projet (`metrics.AssetMetrics.drawdown_max_r`, `circuit_breaker.py`)
   est exprimé en multiples de R, pas en % de capital — aucune mesure de
   drawdown en % n'existe dans la base (la règle de réinvestissement des
   50%, §2.3, rend un "% de l'enveloppe" glissant et non trivial à
   définir sans introduire une nouvelle table de suivi). Approximation
   retenue ici : drawdown_%  ≈  |drawdown_max_r| × risk_percent (le
   risque en % appliqué par trade, `RISK_PERCENT_DEFAULT`, 2% par
   défaut) — cohérent avec le modèle mental du §2.3 (1R ≈ risk_percent%
   du capital engagé), mais reste une approximation, pas une mesure
   directe. `risk_percent` est un paramètre explicite de ce module,
   jamais lu depuis un fichier `.env` en direct (module de reporting,
   pas de dépendance de configuration cachée).
2. **Spread médian** : `market_snapshots.spread` existe dans le schéma
   (§4.5) mais n'est alimenté par AUCUN code du projet à ce jour — ni
   `executor.py` ni `trend_executor.py` n'écrivent jamais dans
   `market_snapshots`. La condition "spread médian < 15%" est donc
   TOUJOURS indéterminée aujourd'hui pour tous les actifs, jamais
   court-circuitée à `True` : `get_median_spread_ratio` retourne `None`
   en l'absence de donnée, ce qui fait échouer la condition (fail-safe,
   invariant #7 : donnée manquante bloque, ne laisse jamais passer). Cet
   actif restera donc non éligible tant que la capture de spread n'aura
   pas été câblée — gap connu, pas un oubli, voir docs/DECISIONS.md.

Calcul **par (actif, source) séparément**, jamais un agrégat mélangé —
Station X, Flux B (Hypothèse #1), et les hypothèses futures (#2, #3)
sont des sources distinctes dès qu'elles existent.

**Comparaisons multiples (§3.9)** : ce score, lu brut entre plusieurs
actifs/sources en même temps, expose à un biais de sélection multiple
classique (l'actif avec le plus de "chance" ressort en tête). La
correction statistique formelle est la charge d'`hypothesis_engine`
(§3.9, cycle trimestriel), qui n'est pas construit — voir
`MULTIPLE_COMPARISONS_CAVEAT` ci-dessous, affiché tel quel sur le
dashboard tant que ce module n'existe pas.

Couverture 100% exigée (demande explicite d'Ismaël, 20/08/2026) — même
régime que risk_engine.py, y compris sur la partie orchestration I/O
(contrairement à executor.py) : ce module influencera un jour une
décision financière réelle (§2.5), même s'il n'en prend aucune
aujourd'hui.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from src.asset_whitelist import ASSET_WHITELIST
from src.db import connection_scope
from src.metrics import AssetMetrics, get_all_asset_keys, get_asset_metrics
from src.risk_engine import AssetSpec

# Même normalisation que metrics._normalize_source / circuit_breaker_store /
# executor._envelope_source_key — dupliquée plutôt qu'importée (nom privé
# ailleurs), même choix que metrics.py. Voir docs/DECISIONS.md.
#
# Généralisée le 21/08/2026 (voir docs/DECISIONS.md) : ne reconnaissait
# QUE "hypothesis" (H1) — toute nouvelle hypothèse DOIT être ajoutée à
# _KNOWN_HYPOTHESIS_SOURCES ici, sinon son score de confiance serait
# mélangé à celui de Station X.
HYPOTHESIS_SOURCE = "hypothesis"    # Hypothèse #1
HYPOTHESIS3_SOURCE = "hypothesis3"  # Hypothèse #3
HYPOTHESIS2_SOURCE = "hypothesis2"  # Hypothèse #2
HYPOTHESIS4_SOURCE = "hypothesis4"  # Hypothèse #4 (21/08/2026 — validée en démo, non déployée)
HYPOTHESIS5_SOURCE = "hypothesis5"  # Hypothèse #5 (23/08/2026 — non déployée)
_KNOWN_HYPOTHESIS_SOURCES = {HYPOTHESIS_SOURCE, HYPOTHESIS3_SOURCE, HYPOTHESIS2_SOURCE, HYPOTHESIS4_SOURCE, HYPOTHESIS5_SOURCE}


def _normalize_source(source: str) -> str:
    return source if source in _KNOWN_HYPOTHESIS_SOURCES else "stationx"


PHASE_A_MIN_TRADES = 20
PHASE_B_MIN_TRADES = 50
SPREAD_MEDIAN_MAX_RATIO = 0.15
STABILITY_DRAWDOWN_CAP_PCT = 20.0
DEFAULT_RISK_PERCENT = 2.0  # cohérent avec RISK_PERCENT_DEFAULT de .env.example

MULTIPLE_COMPARISONS_CAVEAT = (
    "Score indicatif tant que le nombre de trades reste sous les seuils "
    "d'éligibilité (20 puis 50, §2.4) : avec de petits échantillons, "
    "comparer plusieurs actifs/sources entre eux expose à un biais de "
    "sélection multiple (§3.9). La correction statistique formelle "
    "n'est appliquée qu'une fois hypothesis_engine construit (non "
    "construit à ce jour) — ce classement ne doit pas être lu comme une "
    "comparaison brute et définitive."
)


# ---------------------------------------------------------------------------
# Calcul pur (100% couvert)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EligibilityCheck:
    condition: str
    satisfied: bool
    detail: str


@dataclass(frozen=True)
class ConfidenceScore:
    actif: str
    source: str
    nb_trades: int
    esperance_r: Optional[float]
    facteur_echantillon: float
    facteur_stabilite: float
    score: Optional[float]  # None si non éligible ou espérance indisponible
    eligible: bool
    phase: str  # "A" | "B" | "insuffisant"
    checks: List[EligibilityCheck] = field(default_factory=list)


def compute_sample_factor(nb_trades: int) -> float:
    """√(nb_trades/50), plafonné à 1 — empêche qu'un actif avec quelques
    trades chanceux devance un actif avec un échantillon solide."""
    if nb_trades <= 0:
        return 0.0
    return min(1.0, math.sqrt(nb_trades / PHASE_B_MIN_TRADES))


def compute_stability_factor(drawdown_max_r: float, risk_percent: float = DEFAULT_RISK_PERCENT) -> float:
    """1 − drawdown_%/20%, plancher 0. Voir docstring du module (point 1)
    pour l'approximation drawdown_% ≈ |drawdown_max_r| × risk_percent."""
    drawdown_pct_approx = abs(drawdown_max_r) * risk_percent
    return max(0.0, 1.0 - drawdown_pct_approx / STABILITY_DRAWDOWN_CAP_PCT)


def compute_score(esperance_r: float, sample_factor: float, stability_factor: float) -> float:
    return round(esperance_r * sample_factor * stability_factor, 6)


def check_min_trades(nb_trades: int) -> Tuple[bool, str, str]:
    """(satisfait, détail, phase) — phase in {"A", "B", "insuffisant"}."""
    if nb_trades >= PHASE_B_MIN_TRADES:
        return True, f"{nb_trades} trades ≥ {PHASE_B_MIN_TRADES} (phase B)", "B"
    if nb_trades >= PHASE_A_MIN_TRADES:
        return True, f"{nb_trades} trades ≥ {PHASE_A_MIN_TRADES} (phase A)", "A"
    return False, f"{nb_trades} trades < {PHASE_A_MIN_TRADES} (seuil phase A)", "insuffisant"


def check_esperance_positive(esperance_r: Optional[float]) -> Tuple[bool, str]:
    if esperance_r is None:
        return False, "Espérance indisponible (aucun trade fermé)"
    if esperance_r > 0:
        return True, f"Espérance nette {esperance_r:.4f}R > 0"
    return False, f"Espérance nette {esperance_r:.4f}R ≤ 0"


def check_min_size_compatible(
    capital_courant: Optional[float],
    stop_distance_typical: Optional[float],
    asset_spec: Optional[AssetSpec],
    risk_percent: float = DEFAULT_RISK_PERCENT,
) -> Tuple[bool, str]:
    """Ré-application en lecture seule de la formule de dimensionnement de
    risk_engine.evaluate_new_entry (jamais utilisée pour placer un ordre —
    module de reporting uniquement, invariant #3 concerne le flux
    d'exécution réel, pas une statistique affichée sur un dashboard)."""
    if asset_spec is None:
        return False, "Actif absent de la liste blanche"
    if capital_courant is None:
        return False, "Enveloppe introuvable"
    if stop_distance_typical is None or stop_distance_typical <= 0:
        return False, "Distance de stop typique indisponible (aucun trade avec prix d'entrée et stop renseignés)"

    risk_amount_eur = capital_courant * (risk_percent / 100.0)
    raw_units = risk_amount_eur / (stop_distance_typical * asset_spec.pip_value_per_unit)
    if raw_units >= asset_spec.min_units:
        return True, f"{raw_units:.4f} unités calculables ≥ minimum broker {asset_spec.min_units}"
    return False, f"{raw_units:.4f} unités calculables < minimum broker {asset_spec.min_units}"


def check_spread_condition(median_spread_ratio: Optional[float]) -> Tuple[bool, str]:
    if median_spread_ratio is None:
        return False, "Spread médian indisponible (aucune donnée enregistrée pour cet actif/source à ce jour)"
    if median_spread_ratio < SPREAD_MEDIAN_MAX_RATIO:
        return True, f"Spread médian {median_spread_ratio * 100:.1f}% du stop typique < seuil {SPREAD_MEDIAN_MAX_RATIO * 100:.0f}%"
    return False, f"Spread médian {median_spread_ratio * 100:.1f}% du stop typique ≥ seuil {SPREAD_MEDIAN_MAX_RATIO * 100:.0f}%"


def evaluate_confidence(
    metrics: AssetMetrics,
    capital_courant: Optional[float],
    stop_distance_typical: Optional[float],
    median_spread_ratio: Optional[float],
    asset_spec: Optional[AssetSpec],
    risk_percent: float = DEFAULT_RISK_PERCENT,
) -> ConfidenceScore:
    trades_ok, trades_detail, phase = check_min_trades(metrics.nb_trades)
    esperance_ok, esperance_detail = check_esperance_positive(metrics.esperance_r)
    size_ok, size_detail = check_min_size_compatible(capital_courant, stop_distance_typical, asset_spec, risk_percent)
    spread_ok, spread_detail = check_spread_condition(median_spread_ratio)

    checks = [
        EligibilityCheck("nb_trades", trades_ok, trades_detail),
        EligibilityCheck("esperance_nette", esperance_ok, esperance_detail),
        EligibilityCheck("taille_minimale", size_ok, size_detail),
        EligibilityCheck("spread_median", spread_ok, spread_detail),
    ]
    eligible = all(c.satisfied for c in checks)

    sample_factor = round(compute_sample_factor(metrics.nb_trades), 6)
    stability_factor = round(compute_stability_factor(metrics.drawdown_max_r, risk_percent), 6)
    score = (
        compute_score(metrics.esperance_r, sample_factor, stability_factor)
        if eligible and metrics.esperance_r is not None
        else None
    )

    return ConfidenceScore(
        actif=metrics.actif,
        source=metrics.source,
        nb_trades=metrics.nb_trades,
        esperance_r=metrics.esperance_r,
        facteur_echantillon=sample_factor,
        facteur_stabilite=stability_factor,
        score=score,
        eligible=eligible,
        phase=phase,
        checks=checks,
    )


def _median(values: List[float]) -> float:
    return statistics.median(values)


# ---------------------------------------------------------------------------
# Orchestration I/O — lecture DB uniquement, AUCUN accès broker (même
# invariant que dashboard.py : ce module doit pouvoir tourner sans
# session Capital.com, il en est un consommateur direct)
# ---------------------------------------------------------------------------

def get_capital_courant(db_path: str, actif: str, source: str) -> Optional[float]:
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT capital_courant FROM envelopes WHERE actif = ? AND source = ?",
            (actif, normalized),
        ).fetchone()
    return row["capital_courant"] if row is not None else None


def get_median_stop_distance(db_path: str, actif: str, source: str) -> Optional[float]:
    """Médiane de |prix_entree_reel - stop_loss_initial| sur les trades
    fermés de cet (actif, source) — sert de "stop typique" en l'absence
    d'une notion de stop typique par actif ailleurs dans le projet."""
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT prix_entree_reel, stop_loss_initial, source FROM trades "
            "WHERE actif = ? AND statut = 'ferme' "
            "AND prix_entree_reel IS NOT NULL AND stop_loss_initial IS NOT NULL",
            (actif,),
        ).fetchall()
    distances = [
        abs(row["prix_entree_reel"] - row["stop_loss_initial"])
        for row in rows
        if _normalize_source(row["source"]) == normalized
    ]
    distances = [d for d in distances if d > 0]
    if not distances:
        return None
    return _median(distances)


def get_median_spread_ratio(db_path: str, actif: str, source: str) -> Optional[float]:
    """Spread médian (proportion du stop typique du trade concerné) sur
    les trades fermés de cet (actif, source), via market_snapshots. Voir
    docstring du module (point 2) : retourne None pour tous les
    actifs/sources aujourd'hui — market_snapshots.spread n'est alimenté
    par aucun code du projet à ce jour."""
    normalized = _normalize_source(source)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT ms.spread AS spread, t.prix_entree_reel AS prix_entree_reel, "
            "t.stop_loss_initial AS stop_loss_initial, t.source AS source "
            "FROM market_snapshots ms JOIN trades t ON t.signal_id = ms.signal_id "
            "WHERE t.actif = ? AND t.statut = 'ferme' AND ms.spread IS NOT NULL "
            "AND t.prix_entree_reel IS NOT NULL AND t.stop_loss_initial IS NOT NULL",
            (actif,),
        ).fetchall()
    ratios = []
    for row in rows:
        if _normalize_source(row["source"]) != normalized:
            continue
        stop_distance = abs(row["prix_entree_reel"] - row["stop_loss_initial"])
        if stop_distance > 0:
            ratios.append(row["spread"] / stop_distance)
    if not ratios:
        return None
    return _median(ratios)


def compute_confidence_score(
    db_path: str, actif: str, source: str, risk_percent: float = DEFAULT_RISK_PERCENT
) -> ConfidenceScore:
    metrics = get_asset_metrics(db_path, actif, source)
    capital_courant = get_capital_courant(db_path, actif, metrics.source)
    stop_distance_typical = get_median_stop_distance(db_path, actif, metrics.source)
    median_spread_ratio = get_median_spread_ratio(db_path, actif, metrics.source)
    asset_spec = ASSET_WHITELIST.get(actif)
    return evaluate_confidence(
        metrics, capital_courant, stop_distance_typical, median_spread_ratio, asset_spec, risk_percent
    )


def compute_all_confidence_scores(db_path: str, risk_percent: float = DEFAULT_RISK_PERCENT) -> List[ConfidenceScore]:
    """Un score par (actif, source) ayant une enveloppe créée. Triés :
    éligibles d'abord par score décroissant, puis non-éligibles par
    nb_trades décroissant (les plus proches d'un seuil en tête)."""
    scores = [
        compute_confidence_score(db_path, actif, source, risk_percent)
        for actif, source in get_all_asset_keys(db_path)
    ]
    eligible = sorted((s for s in scores if s.eligible), key=lambda s: s.score, reverse=True)
    non_eligible = sorted((s for s in scores if not s.eligible), key=lambda s: s.nb_trades, reverse=True)
    return eligible + non_eligible
