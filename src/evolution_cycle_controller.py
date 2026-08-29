"""
evolution_cycle_controller.py — Point 8 (29/08/2026, voir
docs/DECISIONS.md) : cycle d'ajustement continu, active au déploiement
pour les 5 hypothèses. Enveloppe `evolution_engine.run_evolution_cycle`
(inchangé dans son cœur statistique) avec les garde-fous OPÉRATIONNELS
demandés, qu'`evolution_engine.py` n'a pas et ne doit pas avoir (il
reste un pur moteur statistique sans notion de calendrier ni de budget
machine) :

1. **Interrupteur DB par hypothèse** (`evolution_cycle_state`) : absent
   = ACTIVÉ par défaut pour les 5 (fail-safe explicite — une ligne
   manquante n'est jamais interprétée comme "désactivé"). Un `/etat`
   futur pourra lire/écrire cette table sans toucher au reste.
2. **Plafond d'UNE validation confirmatoire par hypothèse par 30 jours
   glissants** (`evolution_validations`) — vérifié AVANT tout appel à
   `validation_fn` (jamais après coup). Le compteur CUMULÉ (toutes les
   validations jamais consommées, pas seulement les 30 derniers jours)
   alimente `evolution_engine.bonferroni_one_sided_z` : plus une
   hypothèse a déjà consommé de validations dans le passé, plus le seuil
   z appliqup à CETTE validation est strict — un "regard" répété dans le
   temps est une comparaison multiple au même titre que plusieurs
   candidats simultanés (jamais remis à zéro entre deux cycles).
3. **Budget de temps mur** (`max_wall_seconds`) : vérifié avant CHAQUE
   candidat d'entraînement — interrompt proprement (rapporte un
   résultat partiel explicite) plutôt que de laisser le process tourner
   indéfiniment sur un VPS à 2 cœurs partagés avec les 6 process de
   trading. Le `nice -n 19` lui-même est une responsabilité du
   lanceur (cron), pas de ce module — voir
   `scripts/run_continuous_adjustment_cycle.py`.
4. **Fail-safe PAR HYPOTHÈSE** (`run_all_enabled_cycles`) : une
   exception sur une hypothèse est journalisée et n'empêche jamais les
   4 autres de tourner.

Démo uniquement par construction : ce module n'a aucun accès broker,
capital ou risque (invariant #1) — il appelle `train_fn`/`validation_fn`
injectées par le lanceur (backtest local, jamais un ordre réel), au même
contrat que `evolution_engine.run_evolution_cycle`.

Deux couches, même convention que le reste du projet :
- Calcul pur (`is_deadline_exceeded`) : 100% couvert.
- Orchestration I/O (le reste) : lecture/écriture DB seule.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from src.db import connection_scope
from src.evolution_engine import (
    CandidateSpec,
    EvolutionCycleReport,
    TrainingResult,
    bonferroni_one_sided_z,
    run_evolution_cycle,
)

VALIDATION_WINDOW_DAYS = 30


def _parse_naive_utc(timestamp: str) -> datetime:
    """Même convention que `financing_analysis.py` : dépouille `tzinfo`
    pour comparer des horodatages déjà en UTC, quel que soit le format
    ISO exact (`Z` ou `+00:00`, voir bug du point 4)."""
    return datetime.fromisoformat(timestamp).replace(tzinfo=None)


def is_evolution_cycle_enabled(db_path: str, hypothesis: str) -> bool:
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM evolution_cycle_state WHERE hypothesis = ?", (hypothesis,),
        ).fetchone()
    return True if row is None else bool(row["enabled"])


def set_evolution_cycle_enabled(db_path: str, hypothesis: str, enabled: bool) -> None:
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO evolution_cycle_state (hypothesis, enabled) VALUES (?, ?) "
            "ON CONFLICT(hypothesis) DO UPDATE SET enabled = excluded.enabled",
            (hypothesis, int(enabled)),
        )


def cumulative_validation_count(db_path: str, hypothesis: str) -> int:
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM evolution_validations WHERE hypothesis = ?", (hypothesis,),
        ).fetchone()
    return row["n"]


def count_recent_validations(db_path: str, hypothesis: str, now_iso: str, window_days: int = VALIDATION_WINDOW_DAYS) -> int:
    now = _parse_naive_utc(now_iso)
    cutoff = now - timedelta(days=window_days)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT validated_at FROM evolution_validations WHERE hypothesis = ?", (hypothesis,),
        ).fetchall()
    return sum(1 for row in rows if _parse_naive_utc(row["validated_at"]) >= cutoff)


def record_validation(db_path: str, hypothesis: str, now_iso: str) -> None:
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO evolution_validations (hypothesis, validated_at) VALUES (?, ?)",
            (hypothesis, now_iso),
        )


def is_deadline_exceeded(start_time: float, max_wall_seconds: Optional[float], now: Optional[float] = None) -> bool:
    """Calcul pur (`time.monotonic()` injecté via `now` pour rester
    testable sans dépendre de l'horloge réelle). `max_wall_seconds=None`
    = aucun budget (jamais dépassé) — comportement historique
    d'`evolution_engine.py` avant ce module."""
    if max_wall_seconds is None:
        return False
    now = time.monotonic() if now is None else now
    return (now - start_time) >= max_wall_seconds


@dataclass(frozen=True)
class GuardedCycleOutcome:
    hypothesis: str
    ran: bool
    reason: str
    report: Optional[EvolutionCycleReport] = None


def run_guarded_evolution_cycle(
    hypothesis: str,
    candidates: List[CandidateSpec],
    train_fn: Callable[[CandidateSpec], TrainingResult],
    validation_fn: Callable[[CandidateSpec], TrainingResult],
    min_trades_train: int,
    min_trades_validation: int,
    db_path: str,
    now_iso: str,
    persist: bool = True,
    notify_fn: Optional[Callable[[dict], None]] = None,
    max_wall_seconds: Optional[float] = None,
    start_time: Optional[float] = None,
) -> GuardedCycleOutcome:
    """Applique les 3 garde-fous (interrupteur, plafond 30j, budget de
    temps) puis délègue à `evolution_engine.run_evolution_cycle`.

    Le plafond de validation est vérifié AVANT l'entraînement (pas
    seulement avant l'appel à `validation_fn`) : si le plafond est déjà
    atteint, faire tourner l'entraînement pour rien consommerait du
    budget machine sans jamais pouvoir aboutir à une proposition — voir
    point 8, "un cycle ne doit jamais dégrader l'exécution live"."""
    if not is_evolution_cycle_enabled(db_path, hypothesis):
        return GuardedCycleOutcome(hypothesis, ran=False, reason="Cycle désactivé (evolution_cycle_state)")

    start_time = time.monotonic() if start_time is None else start_time
    if is_deadline_exceeded(start_time, max_wall_seconds):
        return GuardedCycleOutcome(hypothesis, ran=False, reason="Budget de temps déjà épuisé avant démarrage")

    recent = count_recent_validations(db_path, hypothesis, now_iso)
    if recent >= 1:
        return GuardedCycleOutcome(
            hypothesis, ran=False,
            reason=f"Plafond atteint : {recent} validation(s) déjà consommée(s) sur les {VALIDATION_WINDOW_DAYS} derniers jours",
        )

    def budgeted_train_fn(candidate: CandidateSpec) -> TrainingResult:
        if is_deadline_exceeded(start_time, max_wall_seconds):
            raise TimeoutError(f"Budget de temps mur dépassé pendant l'entraînement ({hypothesis})")
        return train_fn(candidate)

    validation_consumed = False

    def counting_validation_fn(candidate: CandidateSpec) -> TrainingResult:
        nonlocal validation_consumed
        validation_consumed = True
        return validation_fn(candidate)

    z = bonferroni_one_sided_z(cumulative_validation_count(db_path, hypothesis) + 1)

    try:
        report = run_evolution_cycle(
            hypothesis, candidates, budgeted_train_fn, counting_validation_fn,
            min_trades_train, min_trades_validation, db_path,
            now_iso=now_iso, persist=persist, notify_fn=notify_fn, validation_z=z,
        )
    except TimeoutError as exc:
        return GuardedCycleOutcome(hypothesis, ran=False, reason=str(exc))

    if validation_consumed:
        record_validation(db_path, hypothesis, now_iso)

    return GuardedCycleOutcome(hypothesis, ran=True, reason="Cycle exécuté", report=report)


def run_all_enabled_cycles(
    hypotheses: Dict[str, dict],
    db_path: str,
    now_iso: str,
    max_wall_seconds: Optional[float] = None,
    notify_fn: Optional[Callable[[dict], None]] = None,
    persist: bool = True,
) -> Dict[str, GuardedCycleOutcome]:
    """`hypotheses` : {nom: {"candidates", "train_fn", "validation_fn",
    "min_trades_train", "min_trades_validation"}}. Fail-safe PAR
    HYPOTHÈSE (point 8) : une exception sur l'une n'empêche jamais les
    autres de tourner — journalisée dans le résultat, jamais propagée."""
    start_time = time.monotonic()
    results: Dict[str, GuardedCycleOutcome] = {}
    for name, cfg in hypotheses.items():
        try:
            results[name] = run_guarded_evolution_cycle(
                name, cfg["candidates"], cfg["train_fn"], cfg["validation_fn"],
                cfg["min_trades_train"], cfg["min_trades_validation"], db_path, now_iso,
                persist=persist, notify_fn=notify_fn, max_wall_seconds=max_wall_seconds, start_time=start_time,
            )
        except Exception as exc:  # noqa: BLE001 - fail-safe explicite, voir docstring
            results[name] = GuardedCycleOutcome(name, ran=False, reason=f"Exception non gérée : {exc!r}")
    return results
