"""
retry.py — nouvelle tentative avec backoff court pour absorber les
erreurs 429/réseau TRANSITOIRES de l'API Capital.com, introduit le
24/08/2026 (voir docs/DECISIONS.md).

Contexte : le rate-limiting Capital.com (429 error.too-many.requests)
s'est aggravé après le déploiement simultané de H2-H5 le 23/08/2026 (6
process concurrents pollant la même IP toutes les ~60s). Deux points de
défaillance identifiés comme particulièrement coûteux, tous deux visés
par ce module :
- la sonde de connectivité générale en début de cycle (technical_
  strategy_executor.py/executor.py) : un seul échec saute TOUT le cycle
  (aucune entrée, aucune gestion des positions ouvertes ce tour-ci) ;
- le rafraîchissement du contexte de régime H3/H4
  (regime_confirmation.compute_index_regimes) : n'ayant lieu qu'aux 3
  ouvertures de session UTC (0h/8h/13h) plus une fois au démarrage, un
  seul échec laisse le cache à None (ou périmé) jusqu'au créneau
  suivant — jusqu'à ~8h de rejets fail-safe "contexte de régime : None"
  qui n'ont rien à voir avec le marché.

Volontairement PAS un décorateur générique appliqué partout : n'enveloppe
que des appels de LECTURE déjà existants, jamais un ordre
(`open_position`/`place_limit_order`/`close_position`/... ne l'utilisent
jamais) — retenter un envoi d'ordre sur un simple timeout risquerait un
double envoi, un risque bien plus grave qu'un cycle sauté. Chaque
appelant choisit explicitement où l'utiliser.

Ne décide jamais quoi trader (invariant #1) : épuise juste des essais
supplémentaires avant de laisser l'appelant retomber sur son
comportement fail-safe déjà en place (invariant #7) — le comportement
en cas d'échec total est inchangé, seule la probabilité d'y arriver
diminue.
"""

import logging
import random
import time
from typing import Callable, Optional, Sequence, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    exceptions: Tuple[Type[BaseException], ...],
    attempts: int = 3,
    delays_seconds: Sequence[float] = (1.0, 2.0),
) -> T:
    """Appelle `fn()`, retente jusqu'à `attempts` tentatives au total si
    elle lève une exception parmi `exceptions` (`len(delays_seconds)`
    doit valoir `attempts - 1` — une pause entre chaque tentative,
    jamais après la dernière). Relève l'exception de la dernière
    tentative telle quelle si toutes échouent — aucun changement de
    comportement pour l'appelant au-delà des essais supplémentaires."""
    for attempt in range(attempts):
        try:
            return fn()
        except exceptions as exc:
            if attempt == attempts - 1:
                raise
            delay = delays_seconds[attempt]
            logger.warning(
                "Tentative %d/%d échouée (%s) — nouvel essai dans %.1fs",
                attempt + 1, attempts, exc, delay,
            )
            time.sleep(delay)


def retry_with_exponential_backoff(
    fn: Callable[[], T],
    *,
    is_retryable: Callable[[BaseException], bool],
    max_attempts: int = 6,
    base_seconds: float = 2.0,
    cap_seconds: float = 120.0,
    max_retry_after_seconds: float = 600.0,
    rng: Callable[[], float] = random.random,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> T:
    """Backoff exponentiel avec jitter complet pour les traitements par lot
    (rafraîchissement des bougies) — jamais pour un ordre. Avant la
    tentative n+1, l'attente vaut `rng() * min(cap, base * 2**n)` (jitter
    complet) ; si l'exception porte un `retry_after` (en-tête Retry-After du
    429), l'attente n'est JAMAIS inférieure à cette valeur (plafonnée à
    `max_retry_after_seconds`). Une exception non retentable est relevée
    aussitôt ; la dernière est relevée telle quelle après `max_attempts`."""
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc) or attempt == max_attempts - 1:
                raise
            delay = rng() * min(cap_seconds, base_seconds * (2 ** attempt))
            retry_after = getattr(exc, "retry_after", None)
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                delay = max(delay, min(float(retry_after), max_retry_after_seconds))
            logger.warning(
                "Tentative %d/%d échouée (%s) — nouvel essai dans %.1fs", attempt + 1, max_attempts, exc, delay,
            )
            (sleep_fn or time.sleep)(delay)
    raise AssertionError("inatteignable")  # pragma: no cover
