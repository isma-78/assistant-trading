"""
simulator_fidelity.py — A8 du bilan du 05/10/2026 : deux frictions live
absentes du simulateur, modélisées HORS de la logique de décision (rien
n'est réimplémenté, `backtest_engine` reçoit seulement un filtre).

1. Refus de resserrement de stop par le broker. Taux de refus FINAL mesuré
   par actif sur les journaux E1 (25/09 19:35 → 05/10, toutes hypothèses
   poolées, docs/BILAN_05-10.md §2.3). Les actifs avec moins de 10 demandes
   prennent le taux global. Tirage pseudo-aléatoire à graine fixe :
   reproductible à l'identique.
2. Plafond de cluster (`circuit_breaker.CLUSTER_EXPOSURE_CAP_EUR`, toutes
   hypothèses confondues). C'est un effet de PORTEFEUILLE : il ne s'applique
   qu'à un ensemble de trades simulés de plusieurs hypothèses, par
   post-traitement chronologique. Limite : un trade bloqué n'est pas
   remplacé par un signal ultérieur de la même hypothèse, ce que le live
   ferait parfois.

Ni le plafond ni le moteur de risque ne sont modifiés : ce module lit leurs
constantes, il ne les change pas.
"""

import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from src.circuit_breaker import CLUSTER_EXPOSURE_CAP_EUR, CORRELATION_CLUSTERS

# Demandes de resserrement et refus finaux par actif, E1 (bilan §2.3).
MEASURED_STOP_REQUESTS: Dict[str, Tuple[int, int]] = {
    "BTCUSD": (48, 22),
    "GOLD": (36, 11),
    "EURUSD": (136, 24),
    "ETHUSD": (31, 2),
    "US100": (43, 1),
    "USDJPY": (18, 0),
    "US30": (2, 2),
}
MIN_REQUESTS_FOR_ASSET_RATE = 10
DEFAULT_SEED = 20261005


def measured_refusal_rates(
    requests: Dict[str, Tuple[int, int]] = MEASURED_STOP_REQUESTS, min_requests: int = MIN_REQUESTS_FOR_ASSET_RATE,
) -> Tuple[Dict[str, float], float]:
    """(taux par actif retenu, taux global). Un actif sous `min_requests`
    demandes n'a pas de taux propre : il prendra le taux global."""
    total_requests = sum(n for n, _ in requests.values())
    total_refusals = sum(r for _, r in requests.values())
    overall = total_refusals / total_requests if total_requests else 0.0
    per_asset = {asset: r / n for asset, (n, r) in requests.items() if n >= min_requests}
    return per_asset, overall


class StopRefusalModel:
    """Filtre pour `replay_hypothesis(stop_update_filter=...)` : renvoie
    False (refus) avec la probabilité mesurée pour l'actif."""

    def __init__(self, rates: Optional[Dict[str, float]] = None, default_rate: Optional[float] = None,
                 seed: int = DEFAULT_SEED):
        measured, overall = measured_refusal_rates()
        self.rates = measured if rates is None else rates
        self.default_rate = overall if default_rate is None else default_rate
        self._rng = random.Random(seed)
        self.requests = 0
        self.refusals = 0

    def rate_for(self, asset: str) -> float:
        return self.rates.get(asset, self.default_rate)

    def __call__(self, asset: str, old_stop: float, new_stop: float) -> bool:
        self.requests += 1
        if self._rng.random() < self.rate_for(asset):
            self.refusals += 1
            return False
        return True


@dataclass(frozen=True)
class PortfolioTrade:
    source: str
    asset: str
    entry_time: str
    exit_time: str
    risk_eur: float
    r_multiple: float


def apply_cluster_cap(
    trades: Iterable[PortfolioTrade], provisional_risk_eur: float,
    cap_eur: float = CLUSTER_EXPOSURE_CAP_EUR, clusters: Dict[str, str] = CORRELATION_CLUSTERS,
) -> Tuple[List[PortfolioTrade], List[PortfolioTrade]]:
    """Rejoue chronologiquement la règle live d'`executor.open_signal` :
    une entrée est refusée si risque déjà engagé dans le cluster + risque
    provisoire (taux boosté × enveloppe, comme en live) > plafond. Un trade
    libère son risque à son heure de sortie. Retourne (gardés, bloqués)."""
    ordered = sorted(trades, key=lambda t: (t.entry_time, t.source, t.asset))
    kept: List[PortfolioTrade] = []
    blocked: List[PortfolioTrade] = []
    for trade in ordered:
        cluster = clusters.get(trade.asset)
        if cluster is None:
            kept.append(trade)
            continue
        engaged = sum(
            k.risk_eur for k in kept
            if clusters.get(k.asset) == cluster and k.entry_time <= trade.entry_time < k.exit_time
        )
        if engaged + provisional_risk_eur > cap_eur:
            blocked.append(trade)
        else:
            kept.append(trade)
    return kept, blocked
