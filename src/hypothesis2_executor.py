"""
hypothesis2_executor.py — Boucle autonome de l'Hypothèse #2.

**REFONTE L2, 29/08/2026** (voir docs/HYPOTHESES.md, pré-enregistrement
du 29/08/2026, et docs/DECISIONS.md) : le déclencheur ICT/Fibonacci/FVG
(`hypothesis2_strategy.py`, wrapper de `ict_strategy.evaluate_entry`,
déployé et actif depuis le 21/08/2026) est REMPLACÉ par une confluence
multi-timeframe EMA/Ichimoku(9/26/52)/RSI(14) sur M15+H1+H4
(`hypothesis2_strategy_v2.evaluate_entry`). L'ancien wrapper est
ARCHIVÉ (voir archive/hypothesis2_strategy.py) — `ict_strategy.py`
lui-même N'EST PAS archivé (son régime structurel BOS/CHoCH reste
réutilisé par H3/L3, voir docs/HYPOTHESES.md). Source live et backtest
changent de `hypothesis2` à `hypothesis2_v2` — **aucune position H2 v1
ouverte au moment de ce changement** (vérifié, voir docs/DECISIONS.md).

**Multi-timeframe** : `extra_resolutions=["HOUR", "HOUR_4"]` (nouveau
paramètre de `technical_strategy_executor.run_technical_strategy_loop`,
29/08/2026) déclenche deux appels `get_candles` supplémentaires (même
actif, profondeur réduite) — `evaluate_entry` de ce module reçoit donc
`(asset, candles_m15, candles_h1, candles_h4)`, pas la signature à une
seule résolution des autres hypothèses.

**Structure de sortie inchangée** (TP1 50%/TP2 30%/trailing, §2.10,
comme depuis le 23/08/2026) — L2 continue d'utiliser ce mécanisme,
calculé directement dans `hypothesis2_strategy_v2.evaluate_entry`.

**Aucune confirmation de régime croisée** — comme l'ancien H2, la
confluence multi-TF de L2 fait déjà elle-même office de filtre de
régime, une confirmation supplémentaire serait redondante.

Process indépendant, compte Capital.com démo dédié ("hypothèse 2").
Identifiants et statut de déploiement VPS inchangés par ce chantier —
voir point A (déploiement suspendu au déblocage du garde-fou de taille).

9 actifs (8 existants + CHFJPY) — voir docs/HYPOTHESES.md.
"""

import src.hypothesis2_strategy_v2 as _h2_mod
from src.executor import HYPOTHESIS2_V2_SOURCE
from src.hypothesis2_strategy_v2 import evaluate_entry
from src.hypothesis_params import apply_overrides
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS2_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

_CHANNEL = "hypothesis2_strategy"
_PROCESS_NAME = "hypothesis2_executor"
_HYPOTHESIS_LABEL = "Hypothèse #2"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : confluence multi-timeframe EMA/Ichimoku/RSI (L2) "
        f"{signal.direction}, entrée={signal.entry_price}, stop={signal.stop_price}, "
        f"TP1={signal.tp1} (1R), TP2={signal.tp2} (2R) (docs/HYPOTHESES.md)"
    )


def run_hypothesis2_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 20) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #2 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis2).

    `startup_offset_seconds=20` (24/08/2026, voir docs/DECISIONS.md) :
    échelonnement des 6 process de production sur la même IP.

    **Overrides du cycle d'évolution** : clé `"H2_v2"`, jamais `"H2"`
    (n'hérite d'aucun paramètre déjà tuné pour l'ancienne logique v1) —
    variables ajustées du pré-enregistrement (`EMA_PERIOD`,
    `RSI_THRESHOLD`, `N_TF`, `SCORE_THRESHOLD`)."""
    apply_overrides(_h2_mod, "H2_v2", db_path, ["EMA_PERIOD", "RSI_THRESHOLD", "N_TF", "SCORE_THRESHOLD"])
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS2_V2_SOURCE,
        assets=HYPOTHESIS2_ASSETS,
        resolution="MINUTE_15",
        entry_fn=evaluate_entry,
        api_key=config.capital_api_key_hypothesis2,
        identifier=config.capital_identifier_hypothesis2,
        password=config.capital_api_password_hypothesis2,
        account_id=config.capital_account_id_hypothesis2,
        channel=_CHANNEL,
        process_name=_PROCESS_NAME,
        hypothesis_label=_HYPOTHESIS_LABEL,
        describe_signal=_describe_signal,
        interval_seconds=interval_seconds,
        startup_offset_seconds=startup_offset_seconds,
        extra_resolutions=["HOUR", "HOUR_4"],
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis2_loop(app_config, db_path=app_config.db_path)
