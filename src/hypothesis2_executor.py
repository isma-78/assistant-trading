"""
hypothesis2_executor.py — Boucle autonome de l'Hypothèse #2 (ICT / Smart
Money Concepts, Option B), validée par Ismaël le 21/08/2026 (voir
docs/HYPOTHESES.md). Détection via ict_strategy.evaluate_entry (régime
structurel BOS/CHoCH depuis le 23/08/2026 — voir docs/DECISIONS.md,
remplace MA200 — + confluence swings fractals/Fibonacci/FVG),
déclencheur INCHANGÉ par tout ce qui suit.

**Sortie basculée le 23/08/2026** (décision explicite d'Ismaël, voir
docs/DECISIONS.md) : `hypothesis2_strategy.evaluate_entry` (pas
`ict_strategy.evaluate_entry` directement) ajoute désormais TP1(1R)/
TP2(2R) au signal, déclenchant le mécanisme §2.10 (TP1/TP2/trailing sur
le reliquat) au lieu du trailing Donchian(20) pur d'origine.

**Couche session/multi-timeframe, 23/08/2026** : résolution M15 (au lieu
de HOUR). La fenêtre de session (0h/8h/13h UTC) ne gate PLUS la
génération de signaux depuis la révision du 23/08/2026 fin de journée
(voir docs/DECISIONS.md/docs/HYPOTHESES.md — l'ancien gate + l'exemption
crypto associée sont devenus obsolètes et ont été retirés) : évaluation
continue, tous les actifs, à chaque cycle. **Aucune confirmation de
régime croisée** (option C — H2 déjà couverte par son propre régime
structurel BOS/CHoCH, une confirmation supplémentaire serait redondante,
voir docs/HYPOTHESES.md).

Process indépendant, compte Capital.com démo dédié ("hypothèse 2"),
**déployé et actif en production depuis le 21/08/2026** (identifiants
dans le `.env` du VPS) — `run_hypothesis2_loop` échoue net (ConfigError,
fail-safe, invariant #7) si l'un des identifiants
`CAPITAL_API_KEY_HYPOTHESIS2`/`CAPITAL_IDENTIFIER_HYPOTHESIS2`/
`CAPITAL_API_PASSWORD_HYPOTHESIS2`/`CAPITAL_ACCOUNT_ID_HYPOTHESIS2`
venait à manquer — jamais un repli silencieux vers un autre jeu
d'identifiants.

8 actifs (les mêmes que les Hypothèses #1 et #3) — voir docs/HYPOTHESES.md.
"""

from src.executor import HYPOTHESIS2_SOURCE
from src.hypothesis2_strategy import evaluate_entry
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS2_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

_CHANNEL = "hypothesis2_strategy"
_PROCESS_NAME = "hypothesis2_executor"
_HYPOTHESIS_LABEL = "Hypothèse #2"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : confluence ICT (swing fractal K=2, zone de Fibonacci, "
        f"FVG) en régime {signal.direction} (structure BOS/CHoCH), entrée={signal.entry_price}, "
        f"stop={signal.stop_price}, TP1={signal.tp1} (1R), TP2={signal.tp2} (2R), reliquat 20% "
        f"sous trailing ATR (docs/HYPOTHESES.md, docs/DECISIONS.md 23/08/2026)"
    )


def run_hypothesis2_loop(config, db_path: str, interval_seconds: int = 60) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #2 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis2)."""
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS2_SOURCE,
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
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis2_loop(app_config, db_path=app_config.db_path)
