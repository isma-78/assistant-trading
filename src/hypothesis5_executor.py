"""
hypothesis5_executor.py — Boucle autonome de l'Hypothèse #5 (confluence
ICT + momentum RSI, régime structurel, sortie progressive §2.10),
validée par Ismaël le 23/08/2026 (voir docs/HYPOTHESES.md — cette
entrée REMPLACE une version plus ancienne du même jour, jamais
déployée). Détection via hypothesis5_strategy.evaluate_entry (délègue le
régime structurel et la confluence ICT à ict_strategy.evaluate_entry,
exige EN PLUS un franchissement du RSI(14) à 50 dans le même sens sur la
même bougie, ajoute TP1(1R)/TP2(2R)), résolution horaire (HOUR —
identique à H2, cette hypothèse ne teste pas de changement de résolution
ni d'entrée, voir docs/HYPOTHESES.md).

Process indépendant, compte Capital.com démo dédié ("hypothèse 5").
Identifiants (`CAPITAL_API_KEY_HYPOTHESIS5`/`CAPITAL_IDENTIFIER_
HYPOTHESIS5`/`CAPITAL_API_PASSWORD_HYPOTHESIS5`/`CAPITAL_ACCOUNT_ID_
HYPOTHESIS5`) fournis par Ismaël le 23/08/2026 (directement dans le
`.env`, jamais dans la conversation) — **déployé sur le VPS** (tmux
`hypothesis5_executor`), ajouté à `scripts/process_watchdog.py`. Voir
docs/DECISIONS.md pour la vérification en direct effectuée avant/après
déploiement. `run_hypothesis5_loop` échoue toujours net (ConfigError,
fail-safe, invariant #7) si ces identifiants venaient à manquer — jamais
un repli silencieux vers un autre jeu d'identifiants.

8 actifs (les mêmes que les Hypothèses #1, #2, #3 et #4) — voir
docs/HYPOTHESES.md.
"""

from src.executor import HYPOTHESIS5_SOURCE
from src.hypothesis5_strategy import evaluate_entry
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS5_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

_CHANNEL = "hypothesis5_strategy"
_PROCESS_NAME = "hypothesis5_executor"
_HYPOTHESIS_LABEL = "Hypothèse #5"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : confluence ICT (swing fractal K=2, zone de Fibonacci, "
        f"FVG) ET franchissement RSI(14)/50, en régime {signal.direction} (structure BOS/CHoCH), "
        f"entrée={signal.entry_price}, stop={signal.stop_price}, TP1={signal.tp1} (1R), "
        f"TP2={signal.tp2} (2R), reliquat 20% sous trailing ATR (docs/HYPOTHESES.md)"
    )


def run_hypothesis5_loop(config, db_path: str, interval_seconds: int = 60) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #5 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis5 — voir docstring du
    module pour l'état actuel de ce prérequis)."""
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS5_SOURCE,
        assets=HYPOTHESIS5_ASSETS,
        resolution="HOUR",
        entry_fn=evaluate_entry,
        api_key=config.capital_api_key_hypothesis5,
        identifier=config.capital_identifier_hypothesis5,
        password=config.capital_api_password_hypothesis5,
        account_id=config.capital_account_id_hypothesis5,
        channel=_CHANNEL,
        process_name=_PROCESS_NAME,
        hypothesis_label=_HYPOTHESIS_LABEL,
        describe_signal=_describe_signal,
        interval_seconds=interval_seconds,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis5_loop(app_config, db_path=app_config.db_path)
