"""
regime_confirmation.py — Confirmation d'alignement directionnel entre
marchés pour les Hypothèses #3 et #4 UNIQUEMENT (§2.11, couche
session/multi-timeframe), décision explicite d'Ismaël le 23/08/2026
(voir docs/DECISIONS.md, docs/HYPOTHESES.md). MODULE CRITIQUE (même
exigence de couverture que risk_engine.py — gate une vraie décision
d'entrée).

**Ce que c'est** : réutilise `trend_strategy.compute_regime` tel quel
(MA200), appliqué à un ou deux indices de confirmation au lieu d'un
nouvel indicateur — le régime de l'indice concorde-t-il avec celui de
l'actif ? Aucune nouvelle logique de calcul, uniquement une seconde
application de la même fonction à un second instrument.

**Ce que ce n'EST PAS** : PAS un classificateur de force de tendance
(un ADX ou équivalent mesurerait autre chose — l'intensité d'une
tendance, pas son alignement entre marchés). Clarification explicite
d'Ismaël, ne jamais présenter l'un pour l'autre dans la documentation
ou les messages d'audit de cette couche.

**H1, H2, H5 ne passent JAMAIS par ce module** — H1 exclue de toute la
couche (déclencheur intact, aucun nouveau champ), H2/H5 déjà couvertes
par leur régime structurel BOS/CHoCH (option C, voir docs/HYPOTHESES.md
du 23/08/2026), une confirmation supplémentaire serait redondante.

Sessions et indices (fixés a priori, voir docs/HYPOTHESES.md) :
- Asie (0h UTC) : indicateur technique seul (le régime déjà calculé par
  l'entrée elle-même) suffit — cette fonction retourne toujours True
  pour cette session, un pass-through, pas un second filtrage.
- Londres (8h)/New York (13h) : ET strict entre le régime de l'actif et
  le(s) indice(s) de confirmation. US30 et US100 confirmés l'un par
  l'autre (jamais par eux-mêmes, un instrument ne peut pas confirmer son
  propre régime) ; les 6 autres actifs de la liste blanche confirmés par
  US30 ET US100 combinés — les deux doivent concorder, extension directe
  du ET déjà retenu pour toute la couche, jamais une règle différente
  pour ce cas. "Moyenne des régimes" écartée : un régime long/short/
  aucun est catégoriel, une moyenne n'a pas de sens dessus.

Aucun LLM (invariant #1) : comparaisons déterministes, fail-safe
(invariant #7 — toute erreur interne devient un ÉCHEC de confirmation,
jamais un signal laissé passer sur une confirmation indéterminée).
"""

from datetime import datetime, timezone
from typing import Tuple

from src.capital_client import CapitalClient
from src.market_data import get_candles
from src.trend_strategy import MA_PERIOD, compute_regime

# Même marge que technical_strategy_executor.CANDLE_COUNT (dupliquée
# plutôt qu'importée — ce module ne doit dépendre d'aucun détail interne
# de technical_strategy_executor.py au-delà de ce qui est strictement
# nécessaire au calcul du régime, même convention que le reste du projet).
_CANDLE_COUNT = MA_PERIOD + 20

_ASIA_HOUR = 0
_LONDON_HOUR = 8
_NY_HOUR = 13


def confirmation_indices(asset: str) -> Tuple[str, ...]:
    """US30/US100 confirmés l'un par l'autre, jamais par eux-mêmes ;
    tout autre actif de la liste blanche confirmé par les deux
    combinés (voir docstring du module)."""
    if asset == "US30":
        return ("US100",)
    if asset == "US100":
        return ("US30",)
    return ("US30", "US100")


def confirm_regime(client: CapitalClient, asset: str, direction: str, resolution: str, now: datetime) -> bool:
    """Point d'entrée unique. Ne lève jamais d'exception : toute erreur
    interne (indice illisible, historique insuffisant, réponse broker
    incomplète) devient un ÉCHEC de confirmation (fail-safe, invariant
    #7) — jamais un signal laissé passer sur un état indéterminé."""
    try:
        return _confirm_regime(client, asset, direction, resolution, now)
    except Exception:
        return False


def _confirm_regime(client: CapitalClient, asset: str, direction: str, resolution: str, now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("`now` doit être timezone-aware (UTC)")
    hour = now.astimezone(timezone.utc).hour

    if hour == _ASIA_HOUR:
        return True

    if hour not in (_LONDON_HOUR, _NY_HOUR):
        # Défensif : n'arrive jamais en usage normal, le gate de session
        # de technical_strategy_executor.py restreint déjà l'appel à
        # {0, 8, 13}. Fail-safe (invariant #7) si jamais atteint malgré
        # tout — un état hors session inattendu bloque, ne laisse jamais
        # passer silencieusement.
        return False

    for index_epic in confirmation_indices(asset):
        candles = get_candles(client, index_epic, resolution=resolution, count=_CANDLE_COUNT)
        if compute_regime(candles) != direction:
            return False
    return True
