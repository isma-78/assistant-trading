"""
hypothesis4_executor.py — Boucle autonome de l'Hypothèse #4.

**REFONTE L4, 29/08/2026** (voir docs/HYPOTHESES.md, pré-enregistrement
du 29/08/2026, et docs/DECISIONS.md) : le déclencheur retour-à-la-
moyenne/Bollinger (`mean_reversion_strategy.py`, VALIDÉE en démo le
21/08/2026) est REMPLACÉ par la divergence prix/RSI(14)+OBV entre deux
pivots fractals causaux (`hypothesis4_strategy_v2.evaluate_entry`).
L'ancien module est ARCHIVÉ (voir archive/mean_reversion_strategy.py),
jamais supprimé. Source live et backtest changent de `hypothesis4` à
`hypothesis4_v2` (jamais mélangées, voir docs/DECISIONS.md point B) —
**aucune position H4 v1 ouverte au moment de ce changement** (vérifié,
voir docs/DECISIONS.md).

**Structure de sortie CHANGÉE** avec ce remplacement : l'ancien
mécanisme dédié (TP fixe unique/stop fixe/aucun trailing,
`ManagementActionType.CLOSE_FULL_TP`) est abandonné — L4 utilise
désormais la structure STANDARD du projet (§2.10, TP1 50% à 1R/TP2 30%
à 2R/reliquat 20% trailing 2×ATR), comme H1-H3, décision explicite du
pré-enregistrement ("H1-H4 gardent la structure standard"). Le signal
retourné est un `TrendSignal` (tp1/tp2 renseignés), plus un
`MeanReversionSignal`/`signal.take_profit` — dispatché par `technical_
strategy_executor._generate_and_queue_signal` exactement comme
H1/H2/H3/H5.

**Aucune confirmation de régime croisée** (`require_regime_
confirmation=False`) : contrairement à l'ancien H4 (mean-reversion
"contre-tendance" nécessitant un régime de fond confirmé), L4 est une
logique de retournement pur sur divergence — le pré-enregistrement du
29/08/2026 ne spécifie aucun filtre de régime, aucun n'est ajouté ici
(invariant #10 : pas de variable hors pré-enregistrement).

Process indépendant, compte Capital.com démo dédié ("hypothèse 4").
Identifiants et statut de déploiement VPS inchangés par ce chantier —
voir point A (déploiement suspendu au déblocage du garde-fou de taille).

9 actifs (8 existants + CHFJPY) — voir docs/HYPOTHESES.md.
"""

import src.hypothesis4_strategy_v2 as _h4_mod
from src.executor import HYPOTHESIS4_V2_SOURCE
from src.hypothesis4_strategy_v2 import evaluate_entry
from src.hypothesis_params import apply_overrides, get_resolution_override
from src.spread_analysis import compute_expensive_hours_by_asset
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS4_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

_CHANNEL = "hypothesis4_strategy"
_PROCESS_NAME = "hypothesis4_executor"
_HYPOTHESIS_LABEL = "Hypothèse #4"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : divergence prix/RSI(14)+OBV (L4) "
        f"{signal.direction}, entrée={signal.entry_price}, stop={signal.stop_price}, "
        f"TP1={signal.tp1}, TP2={signal.tp2} (docs/HYPOTHESES.md)"
    )


def run_hypothesis4_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 40) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #4 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis4).

    `startup_offset_seconds=40` (24/08/2026, voir docs/DECISIONS.md) :
    échelonnement des 6 process de production sur la même IP.

    **Overrides du cycle d'évolution** : clé `"H4_v2"`, jamais `"H4"`
    (n'hérite d'aucun paramètre déjà tuné pour l'ancienne logique v1,
    voir docs/DECISIONS.md point B) — variables ajustées du
    pré-enregistrement (`PIVOT_FRACTAL_N`, `MAX_PIVOT_DISTANCE_BARS`,
    `STOP_ATR_MULT`).

    **Filtre d'heures chères ACTIVÉ (30/08/2026, voir docs/DECISIONS.md,
    point 1)** : H4 est close côté recherche (raccourci point 14, point
    17) mais reste en démo — ce filtre ne peut que réduire un coût déjà
    mesuré et déterministe, jamais rouvrir une conclusion de recherche
    déjà actée."""
    apply_overrides(_h4_mod, "H4_v2", db_path, ["PIVOT_FRACTAL_N", "MAX_PIVOT_DISTANCE_BARS", "STOP_ATR_MULT"])
    # Amendement 29/08/2026 (voir docs/DECISIONS.md) : résolution HOUR,
    # pas MINUTE_15 — la profondeur M15 réelle du broker s'arrête au
    # 2024-01-01 (sondé, uniforme sur les 8 actifs), la fenêtre de
    # découverte 2019-2022 pré-enregistrée exige HOUR (confirmée jusqu'à
    # 2017). L4 est resolution-agnostique, aucun changement de logique.
    resolution = get_resolution_override(db_path, "H4_v2", "entree", "HOUR")
    expensive_hours_by_asset = compute_expensive_hours_by_asset(db_path)
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS4_V2_SOURCE,
        assets=HYPOTHESIS4_ASSETS,
        resolution=resolution,
        entry_fn=evaluate_entry,
        api_key=config.capital_api_key_hypothesis4,
        identifier=config.capital_identifier_hypothesis4,
        password=config.capital_api_password_hypothesis4,
        account_id=config.capital_account_id_hypothesis4,
        channel=_CHANNEL,
        process_name=_PROCESS_NAME,
        hypothesis_label=_HYPOTHESIS_LABEL,
        describe_signal=_describe_signal,
        interval_seconds=interval_seconds,
        require_regime_confirmation=False,
        startup_offset_seconds=startup_offset_seconds,
        expensive_hours_by_asset=expensive_hours_by_asset,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis4_loop(app_config, db_path=app_config.db_path)
