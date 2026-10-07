"""
mesure_effet_e2.py — Étape 5 du mandat autonome du 08/10/2026,
surveillance (lecture seule) du critère de succès ET de l'arrêt
automatique de E2, selon `docs/PROTOCOLE_AUTONOME_08-10.md` §5.

Conçu pour tourner en tâche planifiée SUR LE VPS tant que E2 est à ON
(protocole, étape 7.F — hors périmètre de cette Partie 1, aucun
déploiement ici). Lit UNIQUEMENT la base locale (tables `logs`,
`trades`, `system_state`) — aucun appel broker, aucune écriture, sauf
`--apply-stop` qui remet `system_state.e2_enabled` à `false` (seule
action d'écriture autorisée par ce script, jamais une action broker).

Source des données : `src.execution.stop_tightening_retry` journalise
CHAQUE issue (et tout élargissement bloqué) dans la table `logs`,
module `execution.stop_tightening_retry` — seule donnée disponible
sans accès aux fichiers de log du VPS (voir docs/AUTONOMIE_08-10.md,
étape 4 : le refus de resserrement n'était PAS mesurable avant ce
module précisément pour cette raison).

Référence pré-activation : `docs/REFERENCE_E2_08-10.md` (19,75% poolé,
BILAN_05-10.md §2.3, jamais recalculée ici).
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution.stop_tightening_retry import WIDENING_BLOCKED_MARKER  # noqa: E402

MODULE_NAME = "execution.stop_tightening_retry"
REFERENCE_REFUSAL_RATE_POOLED = 0.19745222929936307  # docs/REFERENCE_E2_08-10.md, jamais recalculée ici
MIN_N_ATTEMPTS_FOR_VERDICT = 30
RELATIVE_IMPROVEMENT_REQUIRED = 0.50
REPEATED_ERROR_WINDOW_MINUTES = 10
REPEATED_ERROR_THRESHOLD = 3


@dataclass(frozen=True)
class E2Assessment:
    n_attempts: int
    n_refusals: int
    refusal_rate: Optional[float]
    widening_detected: bool
    repeated_module_errors: bool
    verdict_possible: bool
    validated: Optional[bool]
    stop_now: bool
    stop_reasons: List[str]


def _parse_field(message: str, field: str) -> Optional[str]:
    marker = f"{field}="
    idx = message.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = message.find(" ", start)
    return message[start:] if end == -1 else message[start:end]


def _fetch_logs(conn: sqlite3.Connection, since_iso: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT timestamp, level, message FROM logs WHERE module = ? AND timestamp >= ? ORDER BY timestamp",
        (MODULE_NAME, since_iso),
    ).fetchall()


def _repeated_errors_in_window(error_timestamps: List[datetime]) -> bool:
    """Vrai si >= REPEATED_ERROR_THRESHOLD occurrences tombent dans une
    fenêtre glissante de REPEATED_ERROR_WINDOW_MINUTES (protocole §5)."""
    window = timedelta(minutes=REPEATED_ERROR_WINDOW_MINUTES)
    for i, t in enumerate(error_timestamps):
        count = sum(1 for other in error_timestamps[i:] if other - t <= window)
        if count >= REPEATED_ERROR_THRESHOLD:
            return True
    return False


def assess(conn: sqlite3.Connection, since_iso: str) -> E2Assessment:
    rows = _fetch_logs(conn, since_iso)

    n_attempts = 0
    n_refusals = 0
    widening_detected = False
    error_timestamps: List[datetime] = []

    for row in rows:
        if row["level"] == "CRITICAL" and WIDENING_BLOCKED_MARKER in row["message"]:
            widening_detected = True
            continue
        attempts_field = _parse_field(row["message"], "attempts")
        succeeded_field = _parse_field(row["message"], "succeeded")
        fallback_field = _parse_field(row["message"], "fallback_single_attempt")
        if fallback_field == "True":
            try:
                error_timestamps.append(datetime.fromisoformat(row["timestamp"]))
            except ValueError:
                pass
        if attempts_field is None or succeeded_field is None:
            continue
        attempts = int(attempts_field)
        if attempts < 1:
            continue  # refusé par risk_engine avant toute tentative broker : pas une "tentative" au sens §5
        n_attempts += 1
        if succeeded_field == "False":
            n_refusals += 1

    refusal_rate = (n_refusals / n_attempts) if n_attempts else None
    verdict_possible = n_attempts >= MIN_N_ATTEMPTS_FOR_VERDICT
    repeated_module_errors = _repeated_errors_in_window(sorted(error_timestamps))

    validated = None
    if verdict_possible and not widening_detected:
        validated = refusal_rate <= REFERENCE_REFUSAL_RATE_POOLED * (1 - RELATIVE_IMPROVEMENT_REQUIRED)

    stop_reasons = []
    if widening_detected:
        stop_reasons.append("élargissement détecté (ELARGISSEMENT_BLOQUE dans logs) — motif suffisant à lui seul")
    if repeated_module_errors:
        stop_reasons.append(f">= {REPEATED_ERROR_THRESHOLD} erreurs inattendues du module en "
                             f"{REPEATED_ERROR_WINDOW_MINUTES} minutes")

    return E2Assessment(
        n_attempts=n_attempts, n_refusals=n_refusals, refusal_rate=refusal_rate,
        widening_detected=widening_detected, repeated_module_errors=repeated_module_errors,
        verdict_possible=verdict_possible, validated=validated,
        stop_now=bool(stop_reasons), stop_reasons=stop_reasons,
    )


def check_429_increase(conn: sqlite3.Connection, since_iso: str, now: datetime) -> bool:
    """Comparaison de TAUX (429 pooled / heure), jamais un compte brut
    (protocole §5), sur une fenêtre de référence de même durée que la
    fenêtre post-activation, immédiatement avant `since`. Proxy : trades
    annulés `rate_limit_429` (même mécanique que `executors_rate_
    limited` de `scripts/rafraichir_historique.py`) — aucune donnée plus
    fine disponible sans accès aux fichiers de log du VPS."""
    since = datetime.fromisoformat(since_iso)
    duration = now - since
    if duration.total_seconds() <= 0:
        return False
    reference_start = since - duration

    def _count(start: datetime, end: datetime) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM trades WHERE annulation_motif = 'rate_limit_429' "
            "AND ouvert_at >= ? AND ouvert_at < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]

    hours = duration.total_seconds() / 3600
    reference_rate = _count(reference_start, since) / hours
    post_rate = _count(since, now) / hours
    return post_rate > reference_rate


def apply_automatic_stop(db_path: str) -> None:
    """Seule action d'écriture de ce script : remet E2 à OFF — jamais
    une action broker, jamais un redémarrage d'exécuteur (même verrou
    que le protocole §7, écriture seule dans `system_state`)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('e2_enabled', 'false', ?) "
            "ON CONFLICT(key) DO UPDATE SET value='false', updated_at=excluded.updated_at",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--since", required=True, help="horodatage ISO d'activation de E2 (jamais avant)")
    parser.add_argument("--apply-stop", action="store_true",
                         help="écrit system_state.e2_enabled='false' si un arrêt automatique est déclenché")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        assessment = assess(conn, args.since)
        rate_429_increased = check_429_increase(conn, args.since, datetime.now(timezone.utc).replace(tzinfo=None))
    finally:
        conn.close()
    if rate_429_increased:
        assessment.stop_reasons.append("hausse du taux de 429 des exécuteurs vs référence pré-activation")

    print(f"Depuis {args.since} : {assessment.n_attempts} tentative(s), {assessment.n_refusals} refus final(aux), "
          f"taux={assessment.refusal_rate}")
    print(f"Référence pré-activation (poolée) : {REFERENCE_REFUSAL_RATE_POOLED:.4f}")
    print(f"Verdict possible (n>={MIN_N_ATTEMPTS_FOR_VERDICT}) : {assessment.verdict_possible}")
    print(f"Validé : {assessment.validated}")
    print(f"Hausse des 429 vs référence : {rate_429_increased}")
    if assessment.stop_reasons:
        print(f"ARRÊT AUTOMATIQUE DÉCLENCHÉ : {assessment.stop_reasons}")
        if args.apply_stop:
            apply_automatic_stop(args.db_path)
            print("system_state.e2_enabled remis à 'false'.")
        return 1
    print("Aucun motif d'arrêt automatique détecté.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
