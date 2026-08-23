"""
regime_confirmation.py — Confirmation d'alignement directionnel entre
marchés pour les Hypothèses #3 et #4 UNIQUEMENT (§2.11, couche
session/multi-timeframe), décision explicite d'Ismaël le 23/08/2026
(voir docs/DECISIONS.md, docs/HYPOTHESES.md). MODULE CRITIQUE (même
exigence de couverture que risk_engine.py — gate une vraie décision
d'entrée).

**RÉVISION MAJEURE du 23/08/2026, fin de journée** (conception corrigée
d'Ismaël, voir docs/HYPOTHESES.md/docs/DECISIONS.md) : la fenêtre de
session (0h/8h/13h UTC) n'est plus une PORTE sur la génération de
signaux — pour AUCUN actif, y compris crypto. C'est désormais un point
de RECALIBRATION PÉRIODIQUE du contexte de régime confirmé, rien de
plus. Conséquence directe : **l'exemption crypto (BTCUSD/ETHUSD,
`CRYPTO_ASSETS`/`confirm_regime`/`_confirm_regime`) ajoutée plus tôt le
23/08/2026 est devenue OBSOLÈTE et a été retirée de ce module** — les 6
autres actifs reçoivent désormais le même traitement continu que la
crypto recevait déjà, donc plus besoin de cas particulier. Ce retrait
est documenté dans docs/DECISIONS.md comme remplacé par cette
correction générale, pas supprimé silencieusement de l'historique (voir
aussi la révision d'HYPOTHESES.md du même jour).

**Ce que c'est** : réutilise `trend_strategy.compute_regime` tel quel
(MA200), appliqué aux deux indices de confirmation (US30, US100) au lieu
d'un nouvel indicateur — le régime de l'indice concorde-t-il avec celui
de l'actif ? Aucune nouvelle logique de calcul, uniquement une seconde
application de la même fonction à ces deux instruments.

**Ce que ce n'EST PAS** : PAS un classificateur de force de tendance
(un ADX ou équivalent mesurerait autre chose — l'intensité d'une
tendance, pas son alignement entre marchés). Clarification explicite
d'Ismaël, ne jamais présenter l'un pour l'autre dans la documentation
ou les messages d'audit de cette couche.

**H1, H2, H5 ne passent JAMAIS par ce module** — H1 exclue de toute la
couche (déclencheur intact, aucun nouveau champ), H2/H5 déjà couvertes
par leur régime structurel BOS/CHoCH (option C, voir docs/HYPOTHESES.md
du 23/08/2026), une confirmation supplémentaire serait redondante.

**Ce module ne connaît plus aucune notion d'heure ni d'état** — pure
fonction de calcul, appelée par l'appelant (`technical_strategy_
executor.py`) au moment où CELUI-CI décide de rafraîchir le contexte
(aux 3 ouvertures de session UTC, plus une fois au démarrage du process
— voir sa docstring). Le résultat du calcul est mis en cache par
l'appelant et réutilisé pour CHAQUE trigger produit entre deux
rafraîchissements, quelle que soit l'heure à laquelle ce trigger se
déclenche — le déclencheur propre à chaque hypothèse (Donchian pour H3,
Bollinger pour H4) est évalué à chaque cycle (~60s), toute la journée,
sur les 8 actifs, jamais bloqué par ce module.

Indices de confirmation (fixés a priori, voir docs/HYPOTHESES.md) : US30
et US100 confirmés l'un par l'autre (jamais par eux-mêmes, un instrument
ne peut pas confirmer son propre régime) ; les 6 autres actifs de la
liste blanche (dont BTCUSD/ETHUSD depuis ce retrait de l'exemption
crypto) confirmés par US30 ET US100 combinés — les deux doivent
concorder, ET strict. "Moyenne des régimes" écartée : un régime
long/short/aucun est catégoriel, une moyenne n'a pas de sens dessus.

Aucun LLM (invariant #1) : comparaisons déterministes, fail-safe
(invariant #7 — toute erreur ou donnée manquante devient un régime NON
confirmé, jamais un signal laissé passer sur un état indéterminé).
"""

from typing import Dict, Optional, Tuple

from src.capital_client import CapitalClient
from src.market_data import get_candles
from src.trend_strategy import MA_PERIOD, compute_regime

# Même marge que technical_strategy_executor.CANDLE_COUNT (dupliquée
# plutôt qu'importée — ce module ne doit dépendre d'aucun détail interne
# de technical_strategy_executor.py au-delà de ce qui est strictement
# nécessaire au calcul du régime, même convention que le reste du projet).
_CANDLE_COUNT = MA_PERIOD + 20

_CONFIRMATION_INDICES: Tuple[str, ...] = ("US30", "US100")


def confirmation_indices(asset: str) -> Tuple[str, ...]:
    """US30/US100 confirmés l'un par l'autre, jamais par eux-mêmes ;
    tout autre actif de la liste blanche confirmé par les deux
    combinés (voir docstring du module)."""
    if asset == "US30":
        return ("US100",)
    if asset == "US100":
        return ("US30",)
    return _CONFIRMATION_INDICES


def compute_index_regimes(client: CapitalClient, resolution: str) -> Dict[str, Optional[str]]:
    """Calcule le régime MA200 (`trend_strategy.compute_regime`) de US30
    et US100 UNE SEULE FOIS — réutilisé ensuite pour dériver le régime
    confirmé de tous les actifs qui en dépendent (`derive_confirmed_
    regime`), au lieu d'un appel réseau par actif comme dans l'ancienne
    conception (jusqu'à 8 appels par rafraîchissement, un par signal
    généré). Seulement 2 appels au total par rafraîchissement, quel que
    soit le nombre d'actifs de l'appelant.

    Fail-safe PAR INDICE (invariant #7) : une erreur sur un indice donne
    None pour cet indice seul (jamais une exception qui remonterait et
    interromprait le rafraîchissement de l'autre) — `derive_confirmed_
    regime` traite déjà None comme "non confirmé"."""
    regimes: Dict[str, Optional[str]] = {}
    for index_epic in _CONFIRMATION_INDICES:
        try:
            candles = get_candles(client, index_epic, resolution=resolution, count=_CANDLE_COUNT)
            regimes[index_epic] = compute_regime(candles)
        except Exception:
            regimes[index_epic] = None
    return regimes


def derive_confirmed_regime(asset: str, index_regimes: Dict[str, Optional[str]]) -> Optional[str]:
    """À partir des régimes déjà calculés des indices (`compute_index_
    regimes`), dérive le régime confirmé pour `asset` : "long"/"short" si
    l'ensemble des indices requis (voir `confirmation_indices`)
    concordent, None sinon (indice manquant, régime indéterminé, ou
    désaccord — fail-safe, invariant #7, jamais un régime confirmé sur
    une base incertaine). Pure, aucun appel réseau, aucune exception
    possible (accès dict via `.get`, jamais un accès direct)."""
    required = [index_regimes.get(index_epic) for index_epic in confirmation_indices(asset)]
    if any(regime not in ("long", "short") for regime in required):
        return None
    if len(set(required)) == 1:
        return required[0]
    return None
