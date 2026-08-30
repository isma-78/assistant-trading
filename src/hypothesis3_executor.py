"""
hypothesis3_executor.py — Boucle autonome de l'Hypothèse #3.

**REFONTE L3, 29/08/2026** (voir docs/HYPOTHESES.md, pré-enregistrement
du 29/08/2026, et docs/DECISIONS.md) : le déclencheur MA200+Donchian(20)
(`hypothesis3_strategy.py`, wrapper de `trend_strategy.evaluate_entry`)
est REMPLACÉ par un pullback en tendance — régime structurel + jambe
d'impulsion RÉUTILISÉS TELS QUELS depuis `ict_strategy._find_regime_
and_leg` (aucune nouvelle logique de régime), entrée sur retour du prix
au niveau de retracement PUIS reprise confirmée
(`hypothesis3_strategy_v2.evaluate_entry`). L'ancien wrapper est
ARCHIVÉ (voir archive/hypothesis3_strategy.py) — `trend_strategy.py`
N'EST PAS archivé (utilitaires partagés) ; `ict_strategy.py` non plus
(déjà réutilisé par L3 ET H2/L2's ancien code). Source live et backtest
changent de `hypothesis3` à `hypothesis3_v2`.

**Aucune confirmation de régime croisée** (`require_regime_
confirmation=False`, changé depuis l'ancien H3) : L3 réutilise déjà le
régime structurel d'`ict_strategy` en interne, une confirmation externe
US30/US100 serait redondante — même raisonnement que H2, non
spécifié dans le pré-enregistrement (invariant #10 : pas de variable
hors pré-enregistrement).

**TRANSITION DES POSITIONS v1 RÉSOLUE** (même mécanisme que H1, voir
`trend_executor.py` et docs/DECISIONS.md, point 2) : H3 avait 6
positions RÉELLEMENT ouvertes au 29/08/2026 (BTCUSD, ETHUSD, GOLD,
EURUSD, US100, GBPUSD, `source='hypothesis3'`). `legacy_sources=
[HYPOTHESIS3_SOURCE]` ci-dessous étend leur surveillance (réconciliation/
remplissage/gestion/`/stop_urgence`) sans retarder le démarrage des
signaux `hypothesis3_v2`, scopés indépendamment par (actif, source).

**Garde-fou statistique à appliquer au moment de la calibration/
confirmation (points F/G/H), pas ici** : L3 élimine par construction les
mouvements sans pullback (les plus forts) — l'espérance devra être
rapportée par signal détecté (régime+pullback réunis, non remplis
comptés 0R) ET par trade exécuté, jamais l'une sans l'autre.

9 actifs (8 existants + CHFJPY) — voir docs/HYPOTHESES.md.
"""

import src.hypothesis3_strategy_v2 as _h3_mod
from src.executor import HYPOTHESIS3_SOURCE, HYPOTHESIS3_V2_SOURCE
from src.hypothesis3_strategy_v2 import evaluate_entry
from src.hypothesis_params import apply_overrides, get_resolution_override
from src.spread_analysis import compute_expensive_hours_by_asset
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS3_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

_CHANNEL = "hypothesis3_strategy"
_PROCESS_NAME = "hypothesis3_executor"
_HYPOTHESIS_LABEL = "Hypothèse #3"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : pullback en tendance (L3) {signal.direction}, "
        f"entrée={signal.entry_price}, stop={signal.stop_price}, "
        f"TP1={signal.tp1} (1R), TP2={signal.tp2} (2R) (docs/HYPOTHESES.md)"
    )


def run_hypothesis3_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 30) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #3 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis3), résolution M15.

    `startup_offset_seconds=30` (24/08/2026, voir docs/DECISIONS.md) :
    échelonnement des 6 process de production sur la même IP.

    **Overrides du cycle d'évolution** : clé `"H3_v2"`, jamais `"H3"`
    (n'hérite d'aucun paramètre déjà tuné pour l'ancienne logique v1) —
    variables ajustées du pré-enregistrement (`RETRACEMENT_RATIO`,
    `CONFIRMATION_BARS`, `STOP_BUFFER_ATR`).

    **Filtre d'heures chères ACTIVÉ (30/08/2026, voir docs/DECISIONS.md,
    point 1)** : H3 est close côté recherche (raccourci point 14, point
    17) mais reste en démo — ce filtre ne peut que réduire un coût déjà
    mesuré et déterministe, jamais rouvrir une conclusion de recherche
    déjà actée."""
    apply_overrides(_h3_mod, "H3_v2", db_path, ["RETRACEMENT_RATIO", "CONFIRMATION_BARS", "STOP_BUFFER_ATR"])
    # Amendement 29/08/2026 (voir docs/DECISIONS.md) : résolution HOUR,
    # pas MINUTE_15 — la profondeur M15 réelle du broker s'arrête au
    # 2024-01-01 (sondé, uniforme sur les 8 actifs), la fenêtre de
    # découverte 2019-2022 pré-enregistrée exige HOUR (confirmée jusqu'à
    # 2017). L3 est resolution-agnostique, aucun changement de logique.
    resolution = get_resolution_override(db_path, "H3_v2", "entree", "HOUR")
    expensive_hours_by_asset = compute_expensive_hours_by_asset(db_path)
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS3_V2_SOURCE,
        assets=HYPOTHESIS3_ASSETS,
        resolution=resolution,
        entry_fn=evaluate_entry,
        api_key=config.capital_api_key_hypothesis3,
        identifier=config.capital_identifier_hypothesis3,
        password=config.capital_api_password_hypothesis3,
        account_id=config.capital_account_id_hypothesis3,
        channel=_CHANNEL,
        process_name=_PROCESS_NAME,
        hypothesis_label=_HYPOTHESIS_LABEL,
        describe_signal=_describe_signal,
        interval_seconds=interval_seconds,
        require_regime_confirmation=False,
        startup_offset_seconds=startup_offset_seconds,
        legacy_sources=[HYPOTHESIS3_SOURCE],
        expensive_hours_by_asset=expensive_hours_by_asset,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis3_loop(app_config, db_path=app_config.db_path)
