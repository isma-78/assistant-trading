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
"""

from src.risk_engine import AssetSpec

# Taux de change spot observés le 16/08/2026 lors de l'extraction Capital.com
# (voir data/instrument_specs.json). Snapshot statique, pas un flux live.
_EURUSD_SPOT = 1.1570
_USDJPY_SPOT = 159.30

_USD_TO_EUR = 1 / _EURUSD_SPOT                     # ≈ 0.8643 EUR pour 1 USD
_EURJPY_SPOT = _EURUSD_SPOT * _USDJPY_SPOT          # ≈ 184.32
_JPY_TO_EUR = 1 / _EURJPY_SPOT                      # ≈ 0.005426 EUR pour 1 JPY

# pip_value_per_unit = valeur EUR d'un mouvement de prix de 1.0 unité brute,
# pour size=1. Pour tous les instruments cotés en USD (or, indices, FX vs
# USD, crypto), size=1 unité brute x 1.0 USD de mouvement = 1 USD de profit,
# donc la même constante _USD_TO_EUR s'applique. Seul USD/JPY (coté en JPY)
# diffère.
ASSET_WHITELIST = {
    "GOLD": AssetSpec(
        symbol="GOLD",
        min_units=0.01,
        pip_value_per_unit=_USD_TO_EUR,
    ),
    "US100": AssetSpec(
        symbol="US100",
        min_units=0.001,
        pip_value_per_unit=_USD_TO_EUR,
    ),
    "US30": AssetSpec(
        symbol="US30",
        min_units=0.001,
        pip_value_per_unit=_USD_TO_EUR,
    ),
    "EURUSD": AssetSpec(
        symbol="EURUSD",
        min_units=100,
        pip_value_per_unit=_USD_TO_EUR,
    ),
    "GBPUSD": AssetSpec(
        symbol="GBPUSD",
        min_units=100,
        pip_value_per_unit=_USD_TO_EUR,
    ),
    "USDJPY": AssetSpec(
        symbol="USDJPY",
        min_units=100,
        pip_value_per_unit=_JPY_TO_EUR,
    ),
    "BTCUSD": AssetSpec(
        symbol="BTCUSD",
        min_units=0.0001,
        pip_value_per_unit=_USD_TO_EUR,
    ),
    "ETHUSD": AssetSpec(
        symbol="ETHUSD",
        min_units=0.001,
        pip_value_per_unit=_USD_TO_EUR,
    ),
}
