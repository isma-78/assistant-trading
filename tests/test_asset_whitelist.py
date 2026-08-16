"""
Tests de la liste blanche d'actifs (src/asset_whitelist.py). Ne teste pas
une formule financière (le calcul exact de pip_value_per_unit n'est pas
encore validé par le CDC v4, voir docstring du module) — vérifie seulement
que la liste blanche est structurellement cohérente avec le CDC v4 §1.2 et
les invariants du projet.
"""

from src.asset_whitelist import ASSET_WHITELIST, build_asset_whitelist

EXPECTED_ASSETS = {
    "GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD",
}


def test_contains_all_cdc_targets():
    assert set(ASSET_WHITELIST.keys()) == EXPECTED_ASSETS


def test_no_weekend_synthetic_market_used():
    for symbol, spec in ASSET_WHITELIST.items():
        assert not symbol.endswith("_W"), f"{symbol} est un epic week-end synthétique"
        assert spec.weekend_tradable is False, (
            f"{symbol} : weekend_tradable doit rester False tant que la "
            "consigne projet sur les marchés _W n'est pas levée explicitement"
        )


def test_all_specs_construct_validly():
    # AssetSpec.__post_init__ lève déjà si min_units/pip_value_per_unit <= 0 ;
    # ce test échouerait à l'import du module si une valeur était invalide.
    for spec in ASSET_WHITELIST.values():
        assert spec.min_units > 0
        assert spec.pip_value_per_unit > 0


def test_build_asset_whitelist_uses_provided_rates():
    whitelist = build_asset_whitelist(usd_to_eur=0.9, jpy_to_eur=0.006)
    assert set(whitelist.keys()) == EXPECTED_ASSETS
    assert whitelist["GOLD"].pip_value_per_unit == 0.9
    assert whitelist["EURUSD"].pip_value_per_unit == 0.9
    assert whitelist["USDJPY"].pip_value_per_unit == 0.006


def test_build_asset_whitelist_preserves_min_units_from_default():
    whitelist = build_asset_whitelist(usd_to_eur=1.0, jpy_to_eur=1.0)
    for symbol, spec in whitelist.items():
        assert spec.min_units == ASSET_WHITELIST[symbol].min_units
