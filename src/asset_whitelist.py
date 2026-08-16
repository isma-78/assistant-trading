"""
asset_whitelist.py — Liste blanche des actifs tradables (CDC v4 §1.2),
construite à partir des specs réelles Capital.com extraites via
discover_instruments.py le 16/08/2026 (voir data/instrument_specs.json).

DÉCISION D'INGÉNIEUR NON ISSUE DU CDC v4 (à faire confirmer par Ismaël,
CDC v4 non disponible dans ce dépôt — invariant #10 du projet) :

pip_value_per_unit est la valeur en EUR d'un mouvement de prix de 1.0 unité
brute (ex : EUR/USD de 1.1570 à 2.1570), pour 1 unité de taille de position
(size=1 côté Capital.com). Calculée ici via les taux de change spot USD/EUR
et JPY/EUR observés au moment de l'extraction, PAS via une formule écrite
du CDC v4 (indisponible). Cette approximation statique (non rafraîchie en
continu) est jugée sans risque pour la décision d'inclusion/exclusion : avec
les tailles minimales réelles Capital.com, le risque plancher de CHAQUE
actif ci-dessous (min_units x pip_value_per_unit x distance de stop
plausible) reste au moins ~20x sous le seuil d'exclusion de 10€ (2% de
l'enveloppe de 500€, cf §1.2), quelle que soit l'hypothèse retenue pour la
taille d'un "pip" par classe d'actif. Une imprécision de quelques % sur le
taux de change ne peut donc inverser aucune décision d'inclusion ici.

À REVOIR si l'enveloppe ou les seuils de risque changent significativement,
ou si le CDC v4 fournit une formule différente — dans ce cas, remplacer les
constantes ci-dessous plutôt que la logique de RiskEngine.

Aucun marché "_W" (week-end synthétique) n'est utilisé ici, conformément à
la consigne projet : discover_instruments.py écarte systématiquement ces
epics du candidat principal.

TODO BLOQUANT LEVÉ le 16/08/2026 (palier P2) : _USD_TO_EUR et _JPY_TO_EUR
ci-dessous restent des constantes figées, désormais utilisées UNIQUEMENT
comme valeur par défaut de `ASSET_WHITELIST` (décision de liste blanche —
marge de ~20x, sans impact sur cette décision-là, justification ci-dessus
inchangée). Pour tout dimensionnement réel de position, l'exécuteur doit
appeler `build_asset_whitelist()` ci-dessous avec des taux rafraîchis en
direct via `src/market_data.py` (`get_eur_conversion_rate`) — jamais les
constantes statiques seules. Voir docs/DECISIONS.md (palier P2).
"""

from typing import Dict

from src.risk_engine import AssetSpec

# Taux de change spot observés le 16/08/2026 lors de l'extraction Capital.com
# (voir data/instrument_specs.json). Snapshot statique, valeur par défaut
# uniquement — voir build_asset_whitelist() pour le taux dynamique.
_EURUSD_SPOT = 1.1570
_USDJPY_SPOT = 159.30

_USD_TO_EUR = 1 / _EURUSD_SPOT                     # environ 0.8643 EUR pour 1 USD
_EURJPY_SPOT = _EURUSD_SPOT * _USDJPY_SPOT          # environ 184.32
_JPY_TO_EUR = 1 / _EURJPY_SPOT                      # environ 0.005426 EUR pour 1 JPY

# Taille minimale (min_units) : constante par actif, indépendante du taux de
# change — vient des specs réelles Capital.com (minDealSize), pas concernée
# par le TODO bloquant ci-dessus.
_MIN_UNITS = {
    "GOLD": 0.01,
    "US100": 0.001,
    "US30": 0.001,
    "EURUSD": 100,
    "GBPUSD": 100,
    "USDJPY": 100,
    "BTCUSD": 0.0001,
    "ETHUSD": 0.001,
}

# Devise de cotation par actif : détermine quel taux de conversion EUR
# s'applique à pip_value_per_unit. Pour tous les instruments cotés en USD
# (or, indices, FX vs USD, crypto), size=1 unité brute x 1.0 USD de
# mouvement = 1 USD de profit, donc _USD_TO_EUR s'applique. Seul USD/JPY
# (coté en JPY) diffère.
_QUOTE_CURRENCY = {
    "GOLD": "USD", "US100": "USD", "US30": "USD",
    "EURUSD": "USD", "GBPUSD": "USD", "USDJPY": "JPY",
    "BTCUSD": "USD", "ETHUSD": "USD",
}


def build_asset_whitelist(usd_to_eur: float, jpy_to_eur: float) -> Dict[str, AssetSpec]:
    """Construit la liste blanche avec des taux de conversion EUR fournis
    par l'appelant (typiquement `market_data.get_eur_conversion_rate()`
    en direct). C'est la fonction à utiliser pour tout dimensionnement
    réel — jamais `ASSET_WHITELIST` seul, qui reste figé au 16/08/2026."""
    rates = {"USD": usd_to_eur, "JPY": jpy_to_eur}
    return {
        symbol: AssetSpec(
            symbol=symbol,
            min_units=min_units,
            pip_value_per_unit=rates[_QUOTE_CURRENCY[symbol]],
        )
        for symbol, min_units in _MIN_UNITS.items()
    }


# Valeur par défaut, figée aux taux du 16/08/2026 — suffisante pour la
# décision d'inclusion/exclusion (§1.2) et les tests, PAS pour le
# dimensionnement réel de position (voir TODO ci-dessus et
# build_asset_whitelist()).
ASSET_WHITELIST = build_asset_whitelist(_USD_TO_EUR, _JPY_TO_EUR)
