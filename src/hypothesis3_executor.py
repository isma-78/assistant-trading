"""
hypothesis3_executor.py — Boucle autonome de l'Hypothèse #3
(docs/HYPOTHESES.md, 20/08/2026 : « identique à l'Hypothèse #1, seule
la résolution de bougie change »). MA(200) + Donchian(20) — EXACTEMENT
la même logique de décision que le Flux B (trend_strategy.evaluate_entry,
compute_trailing_stop_channel, réutilisés tels quels, jamais
redupliqués), sur des bougies M15 (`MINUTE_15`) au lieu de H1.

Process indépendant, compte Capital.com démo dédié ("hypothèse 3",
accountId retrouvé le 21/08/2026 via GET /accounts avec la clé H3 déjà
en place — voir docs/DECISIONS.md), totalement isolé de Station X et de
l'Hypothèse #1 : enveloppes, coupe-circuits R et statistiques séparés
par (actif, source="hypothesis3"), jamais mélangés (garanti par la
généralisation de _normalize_source/_envelope_source_key du 21/08/2026,
voir docs/DECISIONS.md — sans ce correctif préalable, les trades de
cette hypothèse auraient été silencieusement comptés comme Station X).

Le trailing (gestion de position, executor._evaluate_position_management)
récupère désormais des bougies M15 pour cette source précise
(executor._TREND_CANDLE_RESOLUTION), pas H1 — bug potentiel identifié et
corrigé avant ce déploiement (voir docs/DECISIONS.md du 21/08/2026).

8 actifs (les mêmes que l'Hypothèse #1) — voir docs/HYPOTHESES.md pour
le raisonnement (compte totalement séparé, aucun risque de collision).
"""

from src.executor import HYPOTHESIS3_SOURCE
from src.technical_strategy_executor import run_technical_strategy_loop
from src.trend_strategy import evaluate_entry

HYPOTHESIS3_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

_CHANNEL = "hypothesis3_strategy"
_PROCESS_NAME = "hypothesis3_executor"
_HYPOTHESIS_LABEL = "Hypothèse #3"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : rupture de canal Donchian(20) en régime {signal.direction} "
        f"(filtre MA200, bougies M15), entrée={signal.entry_price}, stop={signal.stop_price} "
        f"(docs/HYPOTHESES.md)"
    )


def run_hypothesis3_loop(config, db_path: str, interval_seconds: int = 60) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #3 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis3), résolution M15."""
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS3_SOURCE,
        assets=HYPOTHESIS3_ASSETS,
        resolution="MINUTE_15",
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
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis3_loop(app_config, db_path=app_config.db_path)
