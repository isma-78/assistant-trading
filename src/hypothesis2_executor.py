"""
hypothesis2_executor.py — Boucle autonome de l'Hypothèse #2 (ICT / Smart
Money Concepts, Option B), validée par Ismaël le 21/08/2026 (voir
docs/HYPOTHESES.md). Détection via ict_strategy.evaluate_entry (régime
MA(200) + confluence swings fractals/Fibonacci/FVG), résolution horaire
(HOUR — cette hypothèse ne teste pas de changement de résolution,
contrairement à l'Hypothèse #3, voir docs/HYPOTHESES.md, budget de
variables).

Process indépendant, compte Capital.com démo dédié ("hypothèse 2").
**Identifiants Capital.com dédiés non fournis à ce jour** — contrairement
à l'Hypothèse #3, aucune clé API/mot de passe distincts n'ont été
générés pour ce compte (voir docs/DECISIONS.md, 21/08/2026, pour le
détail complet et les options possibles). `run_hypothesis2_loop` échoue
net (ConfigError, fail-safe, invariant #7) tant que
`CAPITAL_API_KEY_HYPOTHESIS2`/`CAPITAL_IDENTIFIER_HYPOTHESIS2`/
`CAPITAL_API_PASSWORD_HYPOTHESIS2`/`CAPITAL_ACCOUNT_ID_HYPOTHESIS2` ne
sont pas renseignés — jamais un repli silencieux vers un autre jeu
d'identifiants.

8 actifs (les mêmes que les Hypothèses #1 et #3) — voir docs/HYPOTHESES.md.
"""

from src.executor import HYPOTHESIS2_SOURCE
from src.ict_strategy import evaluate_entry
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS2_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

_CHANNEL = "hypothesis2_strategy"
_PROCESS_NAME = "hypothesis2_executor"
_HYPOTHESIS_LABEL = "Hypothèse #2"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : confluence ICT (swing fractal K=2, zone de Fibonacci, "
        f"FVG) en régime {signal.direction} (filtre MA200), entrée={signal.entry_price}, "
        f"stop={signal.stop_price} (docs/HYPOTHESES.md)"
    )


def run_hypothesis2_loop(config, db_path: str, interval_seconds: int = 60) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #2 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis2 — voir docstring du
    module pour l'état actuel de ce prérequis)."""
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS2_SOURCE,
        assets=HYPOTHESIS2_ASSETS,
        resolution="HOUR",
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
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis2_loop(app_config, db_path=app_config.db_path)
