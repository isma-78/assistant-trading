"""
evolution_engine.py — Automatise la partie MÉCANIQUE (jamais la partie
théorique) du cycle d'évolution déjà pratiqué à la main pour H2-H5
(25-26/08/2026, voir scripts/evaluate_hypothesis_candidates.py et
docs/HYPOTHESES.md). Construit à la demande explicite d'Ismaël du
26/08/2026 ("chaque hypothèse doit ajuster sa stratégie sur la base des
résultats et repasser un trade") — TRANCHÉ en mode "par lot, statistique"
et non "par trade" (voir docs/DECISIONS.md, 26/08/2026) : apprendre
d'un trade isolé, c'est apprendre du bruit ; ce module ne s'exécute
qu'une fois un lot de trades simulés/réels suffisant accumulé, jamais
après un trade unique.

CE QUE CE MODULE NE FAIT PAS (lignes rouges, invariant #10) :
- Il n'invente AUCUN candidat. La liste de `CandidateSpec` (variable,
  valeurs, JUSTIFICATION THÉORIQUE) est écrite par un humain (Ismaël)
  et/ou pré-enregistrée dans docs/HYPOTHESES.md AVANT tout appel à ce
  module — `CandidateSpec` refuse même de se construire sans théorie
  dès qu'il porte un changement réel (voir `__post_init__`).
- Il ne regarde JAMAIS la validation avant d'avoir choisi un candidat
  sur l'entraînement seul (fuite de données).
- Il ne fait qu'UN SEUL essai de validation, jamais un second si le
  premier échoue.
- Il n'a AUCUN accès broker, capital, ou risque (invariant #1) : il ne
  fait qu'écrire une ligne `rule_changes`, relue par
  `hypothesis_params.py` au PROCHAIN REDÉMARRAGE du process concerné
  (jamais en cours de run, invariant #4) — jamais un ordre.
- **N'écrit plus JAMAIS `statut='applique'` (revirement du 29/08/2026,
  point 5, voir docs/DECISIONS.md)** : `apply_immediately` existait
  (25/08/2026) comme "écart CDC assumé" explicite pour H2-H5 — Ismaël
  est revenu dessus explicitement le 29/08/2026 ("NE s'applique JAMAIS
  automatiquement — Ismaël valide/rejette"). Toute ligne écrite par ce
  module porte désormais `statut='propose'`, sans exception ni bascule —
  la validation humaine (mise à jour manuelle de `rule_changes.statut`,
  puis pré-enregistrement dans docs/HYPOTHESES.md, puis nouveau budget
  de variable consommé) reste entièrement hors de ce module.

Deux couches, même convention que risk_engine.py/backtest_engine.py :
- Calcul pur (bonferroni_one_sided_z, compute_lower_bound,
  select_best_candidate, evaluate_validation, build_rule_change_rows) :
  100% de couverture exigée — ce module influence, in fine, une
  décision de trading réelle au même titre que confidence_scorer.py.
- Orchestration I/O (persist_rule_changes, run_evolution_cycle) :
  `train_fn`/`validation_fn` sont injectées par l'appelant
  (scripts/run_evolution_cycle.py, qui encapsule l'appel réel à
  backtest_engine.replay_hypothesis) — même contrat que `entry_fn` dans
  backtest_engine.py, pour rester testable sans données historiques
  réelles.
"""

import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from src.db import connection_scope


# ---------------------------------------------------------------------------
# Couche pure (100% couverte) — aucune I/O, aucun accès broker/DB/capital.
# ---------------------------------------------------------------------------

def bonferroni_one_sided_z(num_candidates: int, family_alpha: float = 0.05) -> float:
    """Seuil z (test unilatéral) corrigé par Bonferroni pour
    `num_candidates` comparaisons simultanées sur l'entraînement (§3.9,
    invariant #10) — même formule que celle utilisée à la main dans les
    chantiers précédents (m=1 -> z≈1.6449, m=3 -> z≈2.1285, valeurs
    vérifiées par tests/test_evolution_engine.py contre les chiffres déjà
    publiés dans docs/HYPOTHESES.md)."""
    if num_candidates < 1:
        raise ValueError(f"num_candidates doit être >= 1, reçu {num_candidates!r}")
    per_test_alpha = family_alpha / num_candidates
    return statistics.NormalDist().inv_cdf(1 - per_test_alpha)


def compute_lower_bound(r_values: List[float], z: float) -> Optional[float]:
    """Borne basse = moyenne - z * (écart-type / sqrt(n)). None si moins de
    2 valeurs (écart-type indéfini) — fail-safe : jamais traité comme
    qualifiant faute de pouvoir le calculer."""
    n = len(r_values)
    if n < 2:
        return None
    mean = statistics.fmean(r_values)
    stdev = statistics.stdev(r_values)
    return mean - z * (stdev / math.sqrt(n))


def compute_calendar_block_bootstrap_lower_bound(
    r_values: List[float],
    timestamps: List[str],
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: int = 0,
) -> Optional[float]:
    """Borne basse par bootstrap par blocs calendaires (mois), critère de
    promotion au RÉEL durci (27/08/2026, voir docs/DECISIONS.md) — plus
    conservateur que `compute_lower_bound` (normale asymptotique) : ne
    suppose ni normalité, ni indépendance intra-mois (auto-corrélation
    plausible entre trades d'un même régime de marché), seulement
    l'indépendance ENTRE mois (blocs). Rééchantillonne des MOIS entiers,
    jamais des trades individuels, pour préserver toute dépendance
    interne à un mois.

    `timestamps[i]` doit être un timestamp ISO 8601 dont les 7 premiers
    caractères forment "AAAA-MM" (même convention que `ferme_at` en
    base). Déterministe (RNG explicitement seedée) pour un résultat
    reproductible d'un appel à l'autre sur les mêmes données.

    None si moins de 2 blocs calendaires distincts (le bootstrap par
    blocs est indéfini avec un seul bloc — jamais traité comme
    qualifiant faute de pouvoir le calculer, même contrat que
    `compute_lower_bound`)."""
    if len(r_values) != len(timestamps):
        raise ValueError("r_values et timestamps doivent avoir la même longueur")
    if not r_values:
        return None

    blocks: Dict[str, List[float]] = {}
    for r, ts in zip(r_values, timestamps):
        blocks.setdefault(ts[:7], []).append(r)
    block_values = list(blocks.values())
    if len(block_values) < 2:
        return None

    rng = random.Random(seed)
    num_blocks = len(block_values)
    resampled_means: List[float] = []
    for _ in range(n_resamples):
        pooled: List[float] = []
        for _ in range(num_blocks):
            pooled.extend(block_values[rng.randrange(num_blocks)])
        resampled_means.append(statistics.fmean(pooled))
    resampled_means.sort()

    index = int((1 - confidence) * n_resamples)
    index = max(0, min(n_resamples - 1, index))
    return resampled_means[index]


@dataclass(frozen=True)
class CandidateSpec:
    """`overrides` vide == candidat de référence (comportement actuel,
    aucun changement). Tout candidat qui modifie au moins un paramètre
    DOIT porter une justification théorique non vide — refus à la
    construction, pas une validation optionnelle plus tard (invariant
    #10 : la théorie précède la donnée, jamais l'inverse)."""
    name: str
    overrides: Dict[str, object] = field(default_factory=dict)
    theory: str = ""

    def __post_init__(self) -> None:
        if self.overrides and not self.theory.strip():
            raise ValueError(
                f"Candidat {self.name!r} : justification théorique non vide "
                "obligatoire pour tout candidat qui modifie un paramètre "
                "(invariant #10) — écrire la théorie AVANT ce candidat, pas après."
            )


@dataclass(frozen=True)
class TrainingResult:
    candidate: str
    n_trades: int
    r_values: Tuple[float, ...] = ()

    @property
    def mean_r(self) -> Optional[float]:
        return statistics.fmean(self.r_values) if self.r_values else None


@dataclass(frozen=True)
class CandidateSelection:
    selected: Optional[str]
    z_score: float
    reason: str
    qualifying: Tuple[str, ...] = ()


def select_best_candidate(
    results: List[TrainingResult], min_trades: int, family_alpha: float = 0.05,
) -> CandidateSelection:
    """Parmi `results` (un par candidat pré-enregistré, référence 'A'
    incluse), sélectionne celui à l'espérance la plus haute PARMI ceux
    qui qualifient : n >= min_trades ET borne basse (Bonferroni sur
    len(results) comparaisons) > 0. Ne regarde jamais la validation.
    Retourne selected=None si aucun candidat ne qualifie — jamais un
    candidat par défaut choisi faute de mieux."""
    if not results:
        raise ValueError("Aucun résultat d'entraînement fourni.")
    z = bonferroni_one_sided_z(len(results), family_alpha)
    qualifying: List[Tuple[str, float]] = []
    for r in results:
        if r.n_trades < min_trades:
            continue
        lower_bound = compute_lower_bound(list(r.r_values), z)
        if lower_bound is not None and lower_bound > 0:
            qualifying.append((r.candidate, r.mean_r if r.mean_r is not None else float("-inf")))

    if not qualifying:
        return CandidateSelection(
            selected=None, z_score=z,
            reason=(
                f"Aucun candidat ne qualifie (n>={min_trades} ET borne basse "
                f"Bonferroni m={len(results)} z={z:.4f} > 0)."
            ),
        )
    best_name, _ = max(qualifying, key=lambda pair: pair[1])
    return CandidateSelection(
        selected=best_name, z_score=z,
        reason=(
            f"Candidat {best_name!r} retenu : espérance la plus haute parmi les "
            f"candidats qualifiés (borne basse Bonferroni m={len(results)} z={z:.4f} > 0)."
        ),
        qualifying=tuple(name for name, _ in qualifying),
    )


@dataclass(frozen=True)
class ValidationVerdict:
    passed: bool
    reason: str


def evaluate_validation(n_trades: int, r_values: List[float], min_trades: int, z: float) -> ValidationVerdict:
    """Gate de puissance appliqué À CHAQUE validation (point 8,
    29/08/2026, voir docs/DECISIONS.md — renforcement méthodologique
    explicite, remplace l'ancien critère "espérance > 0" qui ne
    corrigeait jamais rien). Un seul essai (un seul candidat testé ici),
    mais désormais n >= min_trades ET BORNE BASSE (moyenne - z×erreur-
    type) > 0 — pas la moyenne brute.

    `z` doit intégrer le compteur cumulé de validations déjà consommées
    pour cette hypothèse (voir `evolution_cycle_controller.py`) : plus
    une hypothèse a déjà été validée par le passé, plus `z` doit être
    grand (Bonferroni sur le nombre cumulé de "regards" pris dans le
    temps, pas seulement sur les candidats d'un seul cycle) — ce module
    ne calcule PAS `z` lui-même (aucun accès DB ici), il l'applique tel
    que fourni."""
    if n_trades < min_trades:
        return ValidationVerdict(False, f"{n_trades} trades < seuil validation {min_trades}")
    lower_bound = compute_lower_bound(r_values, z)
    if lower_bound is None or lower_bound <= 0:
        lb_str = f"{lower_bound:.4f}R" if lower_bound is not None else "N/A"
        return ValidationVerdict(False, f"borne basse (z={z:.4f}) {lb_str} <= 0")
    return ValidationVerdict(True, f"{n_trades} trades >= {min_trades}, borne basse (z={z:.4f}) {lower_bound:.4f}R > 0")


def build_rule_change_rows(
    hypothesis: str, candidate: CandidateSpec, constat_stat: str, now_iso: str,
) -> List[dict]:
    """Une ligne `rule_changes` par paramètre du candidat retenu (schéma
    mono-variable par ligne, §3.8) — toutes partagent `constat_stat` et
    l'horodatage pour rester traçables comme un seul lot.

    Toujours `statut='propose'`, `validated_at=None`, `applied_at=None`
    (point 5, 29/08/2026, voir docstring du module — plus de paramètre
    `apply_immediately`, ce module ne sait plus écrire `'applique'`).
    Le candidat de référence 'A' (sans override) n'a rien à écrire —
    ValueError plutôt qu'une ligne vide silencieuse."""
    if not candidate.overrides:
        raise ValueError(
            f"Le candidat {candidate.name!r} n'a aucun override à écrire "
            "(candidat de référence, rien à déployer)."
        )
    rows = []
    for param_name, value in candidate.overrides.items():
        rows.append({
            "proposed_at": now_iso,
            "variable": f"{hypothesis}.{param_name}",
            "constat_stat": constat_stat,
            "ajustement_propose": str(value),
            "statut": "propose",
            "validated_at": None,
            "applied_at": None,
        })
    return rows


# ---------------------------------------------------------------------------
# Orchestration I/O — écrit UNIQUEMENT dans rule_changes. Aucun accès
# broker/capital/risque (invariant #1) : ce module ne décide jamais d'un
# ordre, il propose un paramètre de STRATÉGIE relu au redémarrage suivant.
# ---------------------------------------------------------------------------

def persist_rule_changes(
    db_path: str, rows: List[dict], notify_fn: Optional[Callable[[dict], None]] = None,
) -> List[int]:
    """`notify_fn` (point 5, 29/08/2026) : appelée une fois PAR ligne
    écrite, APRÈS le commit (jamais avant — ne jamais notifier une
    proposition qui aurait échoué à s'écrire). Reçoit la ligne complète
    (dict), donc `constat_stat` — le chiffre qui motive la proposition —
    est toujours dans le message envoyé. Callable injectée (même
    convention que `train_fn`/`validation_fn`) : ce module reste sans
    accès réseau/config Telegram propre, l'appelant (script) construit
    `notify_fn` avec de vrais identifiants."""
    ids: List[int] = []
    with connection_scope(db_path) as conn:
        for row in rows:
            cursor = conn.execute(
                "INSERT INTO rule_changes "
                "(proposed_at, variable, constat_stat, ajustement_propose, statut, validated_at, applied_at) "
                "VALUES (:proposed_at, :variable, :constat_stat, :ajustement_propose, :statut, :validated_at, :applied_at)",
                row,
            )
            ids.append(cursor.lastrowid)
    if notify_fn is not None:
        for row in rows:
            notify_fn(row)
    return ids


@dataclass(frozen=True)
class EvolutionCycleReport:
    hypothesis: str
    training_results: List[TrainingResult]
    selection: CandidateSelection
    validation: Optional[ValidationVerdict]
    applied_rule_change_ids: List[int]


def run_evolution_cycle(
    hypothesis: str,
    candidates: List[CandidateSpec],
    train_fn: Callable[[CandidateSpec], TrainingResult],
    validation_fn: Callable[[CandidateSpec], TrainingResult],
    min_trades_train: int,
    min_trades_validation: int,
    db_path: str,
    now_iso: Optional[str] = None,
    persist: bool = True,
    notify_fn: Optional[Callable[[dict], None]] = None,
    validation_z: float = 1.6449,
) -> EvolutionCycleReport:
    """Orchestrateur mécanique complet : entraînement (tous les
    candidats) -> sélection (entraînement seul) -> validation (une seule
    fois, seulement si un candidat autre que la référence a été
    sélectionné, gate de puissance appliqué avec `validation_z`) ->
    écriture rule_changes, TOUJOURS `statut='propose'` (point 5,
    29/08/2026 — plus d'application automatique, voir docstring du
    module), avec notification optionnelle par ligne écrite. Ne fait
    jamais rien de plus qu'un seul essai de validation par appel.

    `validation_z` (point 8, 29/08/2026) : défaut = Bonferroni m=1 (pas
    de correction), pour un appel isolé/test. En production, TOUJOURS
    fourni par `evolution_cycle_controller.py`, calculé sur le nombre
    CUMULÉ de validations déjà consommées pour cette hypothèse — ce
    module reste sans accès à cet historique par construction (aucun
    accès DB au-delà de `rule_changes`)."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    training_results = [train_fn(c) for c in candidates]
    selection = select_best_candidate(training_results, min_trades_train)

    if selection.selected is None:
        return EvolutionCycleReport(hypothesis, training_results, selection, None, [])

    selected_candidate = next(c for c in candidates if c.name == selection.selected)
    if not selected_candidate.overrides:
        # La référence a gagné : aucun changement à proposer, aucune
        # validation consommée pour rien.
        return EvolutionCycleReport(hypothesis, training_results, selection, None, [])

    val_result = validation_fn(selected_candidate)
    validation = evaluate_validation(val_result.n_trades, list(val_result.r_values), min_trades_validation, validation_z)

    if not validation.passed:
        return EvolutionCycleReport(hypothesis, training_results, selection, validation, [])

    constat_stat = (
        f"Cycle d'évolution {hypothesis} : candidat {selected_candidate.name!r} qualifié "
        f"sur l'entraînement ({selection.reason}) puis validé ({validation.reason})."
    )
    if not persist:
        # Dry-run : calcule tout, n'écrit rien.
        return EvolutionCycleReport(hypothesis, training_results, selection, validation, [])
    rows = build_rule_change_rows(hypothesis, selected_candidate, constat_stat, now_iso)
    ids = persist_rule_changes(db_path, rows, notify_fn=notify_fn)
    return EvolutionCycleReport(hypothesis, training_results, selection, validation, ids)
