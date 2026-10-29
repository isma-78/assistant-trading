"""
hypothesis_asset_map.py — actifs autorisés à ENTRER, par hypothèse
(analyse du 29/10/2026). Source : docs/adequation_paires_2026-10-29.md
(commit 041264c) et docs/corrections_hypotheses_2026-10-29.md §3, calculé
par scripts/_adequation_structurelle_29-10.py (data/analysis_29-10/
structural.json). Mapping codé en dur : modifiable uniquement par config +
redéploiement (invariant #6).

Seules les ENTRÉES sont restreintes. La gestion des positions déjà ouvertes
(stops, trailing, enveloppes, réconciliation) continue sur `ALL_ASSETS` :
aucun orphelin possible. Liste vide = hypothèse suspendue (aucune entrée).
"""

from typing import Dict, List

ALL_ASSETS: List[str] = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
MAX_ASSETS_PER_HYPOTHESIS = 4

HYPOTHESIS_ENTRY_ASSETS: Dict[str, List[str]] = {
    "H1": ["GBPUSD", "USDJPY"],
    "H2": ["GBPUSD", "USDJPY"],
    "H3": ["GBPUSD", "USDJPY"],
    "H4": ["US30", "EURUSD"],
    "H5": ["GBPUSD", "USDJPY"],
}


def entry_assets_for(hypothesis: str) -> List[str]:
    """Actifs d'entrée de l'hypothèse. Hypothèse inconnue => liste vide
    (fail-safe : aucune entrée)."""
    return list(HYPOTHESIS_ENTRY_ASSETS.get(hypothesis, []))
