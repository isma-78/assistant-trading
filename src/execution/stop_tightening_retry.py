"""
stop_tightening_retry.py — E2, retry adaptatif du resserrement de stop
(`docs/PROTOCOLE_AUTONOME_08-10.md` §4, paramètres figés par le
protocole, aucun ajouté ni ajusté ici).

Interrupteur de configuration versionné, **OFF par défaut**
(`system_state.e2_enabled`, lu fail-safe — absence de ligne, table
absente, ou toute erreur SQL = OFF, jamais une activation faute de
donnée). Le verrou d'arrêt d'urgence du protocole (§7) écrit
`e2_enabled='false'` dans cette même table, lu au cycle suivant.

Ne décide jamais QUOI resserrer (invariant #1) : enveloppe un appel de
mise à jour de stop déjà DÉCIDÉ et VALIDÉ par l'appelant
(`risk_engine.evaluate_stop_update` en amont, comme avant ce module),
et retente ce même appel jusqu'à 3 fois (2s puis 5s de délai) pour
absorber une défaillance transitoire — jamais pour élargir, jamais pour
deviner une nouvelle valeur. `update_fn` (fourni par l'appelant)
effectue le SEUL appel broker réel ; ce module ne connaît ni
`CapitalClient` ni le réseau.

Garde-fous (protocole §4) :
- 3 tentatives maximum, délais 2s puis 5s entre elles ;
- arrêt IMMÉDIAT sur tout 429, aucune tentative supplémentaire, le
  stop actuel reste en place (retenté au cycle suivant comme avant E2) ;
- chaque tentative de CETTE séquence doit être au moins aussi
  protectrice que la précédente (jamais un repli vers une valeur moins
  protectrice) — revalidé via `risk_engine.evaluate_stop_update` avant
  la première tentative, **jamais contourné** ;
- refus du broker (toutes tentatives épuisées) : le stop ACTUEL reste
  inchangé, jamais une valeur par défaut ni une estimation ;
- erreur inattendue DU MODULE LUI-MÊME (jamais une erreur broker
  normale, qui pilote déjà la boucle de retry) : repli sur une seule
  tentative, comme avant ce module, journalisée, jamais un crash de la
  boucle de gestion appelante.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from src.db import get_connection

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
DELAYS_SECONDS = (2.0, 5.0)
_RATE_LIMIT_MARKERS = ("too-many.requests", "429")
E2_ENABLED_KEY = "e2_enabled"


class StopWideningBlocked(Exception):
    """Une tentative proposerait un stop moins protecteur que la
    précédente de cette séquence — jamais contourné, jamais avalé par
    le repli générique du module (voir `attempt_with_retry`)."""


@dataclass(frozen=True)
class StopTighteningOutcome:
    succeeded: bool
    attempts: int
    final_stop: float  # stop réellement en place après l'appel : la cible si succès, l'actuel sinon
    aborted_on_rate_limit: bool = False
    fallback_single_attempt: bool = False
    last_error: Optional[str] = None


def is_e2_enabled(db_path: str) -> bool:
    """Fail-safe total : toute absence/erreur -> False (OFF)."""
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (E2_ENABLED_KEY,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    return row is not None and row["value"] == "true"


def _is_rate_limited(error_text: str) -> bool:
    return any(marker in error_text for marker in _RATE_LIMIT_MARKERS)


def _log_outcome(db_path: str, trade_id: Optional[int], outcome: "StopTighteningOutcome") -> None:
    """Trace dédiée (table `logs`, module `execution.stop_tightening_
    retry`), SEUL moyen pour `scripts/mesure_effet_e2.py` (lecture seule)
    de vérifier le critère de succès/arrêt automatique du protocole §5
    sans accès aux fichiers de log du VPS. Uniquement quand E2 est ON
    (jamais sur le chemin OFF, qui reste un passage direct). Best-effort
    : une erreur d'écriture n'interrompt jamais la boucle de gestion
    (fail-safe, même régime que le reste de ce module), jamais de
    valeur sensible journalisée (aucun secret dans ce contexte de toute
    façon — seulement des nombres et booléens)."""
    message = (
        f"trade_id={trade_id} succeeded={outcome.succeeded} attempts={outcome.attempts} "
        f"aborted_on_rate_limit={outcome.aborted_on_rate_limit} "
        f"fallback_single_attempt={outcome.fallback_single_attempt}"
    )
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                "INSERT INTO logs (timestamp, level, module, message) VALUES (?, 'INFO', ?, ?)",
                (datetime.now(timezone.utc).isoformat(), "execution.stop_tightening_retry", message),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("E2 : écriture de la trace d'issue impossible pour le trade %s", trade_id)


def _is_less_protective(candidate: float, previous: float, direction: str) -> bool:
    """Même convention que `risk_engine.evaluate_stop_update` : pour un
    "long", plus protecteur = plus haut ; pour un "short", plus
    protecteur = plus bas. Direction inconnue -> jamais permissif,
    traité comme moins protecteur par sécurité."""
    if direction == "long":
        return candidate < previous
    if direction == "short":
        return candidate > previous
    return True


def _retry_guarded(
    update_fn: Callable[[], None],
    current_stop: float,
    target_stop: float,
    direction: str,
    risk_engine,
    sleep_fn: Callable[[float], None],
) -> StopTighteningOutcome:
    decision = risk_engine.evaluate_stop_update(current_stop, target_stop, direction)
    if not decision.approved:
        return StopTighteningOutcome(succeeded=False, attempts=0, final_stop=current_stop)

    previous_candidate = current_stop
    last_error: Optional[str] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # La cible est FIXE à travers cette séquence de retry (jamais une
        # nouvelle valeur devinée) ; vérifié explicitement à chaque
        # tentative plutôt que supposé, pour documenter l'invariant en
        # code, pas seulement en commentaire.
        if _is_less_protective(target_stop, previous_candidate, direction):
            raise StopWideningBlocked(
                f"tentative {attempt} ({target_stop}) moins protectrice que la précédente ({previous_candidate})"
            )
        previous_candidate = target_stop
        try:
            update_fn()
            return StopTighteningOutcome(succeeded=True, attempts=attempt, final_stop=target_stop)
        except Exception as exc:  # erreur broker normale : pilote le retry, jamais "inattendue"
            text = str(exc)
            if _is_rate_limited(text):
                logger.warning(
                    "E2 : 429 à la tentative %d/%d — arrêt immédiat, stop actuel conservé", attempt, MAX_ATTEMPTS
                )
                return StopTighteningOutcome(
                    succeeded=False, attempts=attempt, final_stop=current_stop,
                    aborted_on_rate_limit=True, last_error=text,
                )
            last_error = text
            if attempt < MAX_ATTEMPTS:
                sleep_fn(DELAYS_SECONDS[attempt - 1])

    logger.warning("E2 : %d tentatives épuisées, stop actuel conservé (%s)", MAX_ATTEMPTS, last_error)
    return StopTighteningOutcome(succeeded=False, attempts=MAX_ATTEMPTS, final_stop=current_stop, last_error=last_error)


def attempt_with_retry(
    update_fn: Callable[[], None],
    *,
    db_path: str,
    current_stop: float,
    target_stop: float,
    direction: str,
    risk_engine,
    sleep_fn: Callable[[float], None] = time.sleep,
    trade_id: Optional[int] = None,
) -> StopTighteningOutcome:
    """Point d'entrée unique. Si E2 est OFF (défaut) : UN SEUL appel à
    `update_fn`, exceptions propagées normalement — comportement
    IDENTIQUE à avant ce module, jamais intercepté ici, jamais journalisé
    dans `logs` (aucune écriture nouvelle sur le chemin par défaut).

    Si E2 est ON : jusqu'à `MAX_ATTEMPTS` tentatives validées et
    espacées, l'issue journalisée dans `logs` (seule donnée disponible
    pour `scripts/mesure_effet_e2.py`, lecture seule, sans accès aux
    fichiers de log du VPS). Toute erreur NON PRÉVUE par les gardes
    ci-dessus (un bug de ce module, jamais une erreur broker normale)
    retombe sur un seul appel supplémentaire à `update_fn` (comportement
    d'avant E2) — sauf `StopWideningBlocked`, jamais avalé ni journalisé
    comme une issue normale : un appelant qui déclenche ce garde-fou
    doit le savoir, jamais un repli silencieux vers un élargissement
    possible."""
    if not is_e2_enabled(db_path):
        update_fn()
        return StopTighteningOutcome(succeeded=True, attempts=1, final_stop=target_stop)

    try:
        outcome = _retry_guarded(update_fn, current_stop, target_stop, direction, risk_engine, sleep_fn)
    except StopWideningBlocked:
        raise
    except Exception as exc:
        logger.exception("E2 : erreur inattendue du module — repli sur une seule tentative (comportement d'avant E2)")
        update_fn()
        outcome = StopTighteningOutcome(
            succeeded=True, attempts=1, final_stop=target_stop, fallback_single_attempt=True, last_error=str(exc)
        )
    _log_outcome(db_path, trade_id, outcome)
    return outcome
