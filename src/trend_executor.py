"""
trend_executor.py — Boucle autonome du Flux B (Hypothèse #1,
docs/HYPOTHESES.md, validée par Ismaël le 20/08/2026 ; périmètre étendu
aux 8 actifs le 20/08/2026, voir docs/DECISIONS.md). Génère des signaux
via trend_strategy.evaluate_entry sur TOUS les actifs de la liste
blanche, qu'ils soient couverts par Station X ou non, les fait passer
par le MÊME validator.py / risk_engine.py / executor.py que le flux
Station X, dans une enveloppe démo strictement séparée
(source="hypothesis") — les deux flux coexistent délibérément sur un
même actif sans se gêner : enveloppes, coupe-circuits R et plafond
d'exposition sont tous calculés par (actif, source), jamais mélangés
entre les deux flux (vérifié explicitement le 20/08/2026, voir
docs/DECISIONS.md). Seule exception assumée, pré-existante et non
changée ici : la pause manuelle `/pause [actif]` (§7.1) reste par
construction commune aux deux flux, jamais scopée par source.

Process indépendant de executor.py, tourne dans sa propre session tmux
sur le VPS, aux côtés de telegram_listener et executor_loop (demande
explicite d'Ismaël — pas une fusion dans la boucle Station X). Toutes
les requêtes sur des tables partagées (signaux en attente, trades
ouverts) sont filtrées par source pour ne jamais toucher aux données de
l'autre boucle — voir executor.manage_open_trades/check_pending_fills et
docs/DECISIONS.md pour la discussion des risques de concurrence entre
les deux process sur la même base SQLite.

Aucun LLM dans la décision d'entrée (invariant #1) — trend_strategy est
purement déterministe. Le seul LLM de ce module intervient exactement
comme pour Station X, via executor.manage_open_trades ->
trade_analyzer.analyze_closed_trade (narratif post-trade uniquement).

N=20 (Donchian) et MA(200) sont FIGÉS (docs/HYPOTHESES.md) — jamais
modifiés ici sans une nouvelle entrée datée dans ce fichier et un
redéploiement.

Refactoré le 21/08/2026 (voir docs/DECISIONS.md) : la boucle générique
(gestion des ordres, coupe-circuits, /stop_urgence, enveloppes) est
désormais partagée avec les Hypothèses #3 et #2 via
`technical_strategy_executor.run_technical_strategy_loop` — ce fichier
ne contient plus que les paramètres propres à l'Hypothèse #1 (source,
résolution HOUR, les 8 actifs, texte d'audit détaillé). Comportement
strictement inchangé (mêmes appels, testé par régression).
"""

import logging

from src.executor import HYPOTHESIS_SOURCE
from src.technical_strategy_executor import (
    CANDLE_COUNT,
    _generate_and_queue_signal as _generic_generate_and_queue_signal,
    _has_active_signal_or_trade,
    run_technical_strategy_loop,
)
from src.trend_strategy import MA_PERIOD, evaluate_entry

logger = logging.getLogger(__name__)

# Les 8 actifs de la liste blanche (asset_whitelist.py) — étendu le
# 20/08/2026 depuis les 5 initiaux (US30/EURUSD/GBPUSD/USDJPY/ETHUSD,
# choisis le 19-20/08 comme "sans signal Station X à l'époque") pour
# couvrir GOLD/US100/BTCUSD également, sur décision explicite d'Ismaël :
# les deux flux tournent désormais sur TOUS les actifs, sans exclusivité
# — quand Station X est silencieux sur un actif, le Flux B continue
# quand même dessus (voir docs/DECISIONS.md). Toute évolution de cette
# liste = nouvelle entrée datée dans ce fichier de référence, pas un
# ajustement silencieux de cette constante.
# CHFJPY ajoutée le 28/08/2026 (voir docs/DECISIONS.md, point 2) — ajout
# de PÉRIMÈTRE demandé par Ismaël pour les 5 hypothèses, pas une
# variable de stratégie, ne consomme aucun budget invariant #10.
HYPOTHESIS_ASSETS = ["US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD", "CHFJPY"]

_CHANNEL = "trend_strategy"
_PROCESS_NAME = "trend_executor"
_HYPOTHESIS_LABEL = "Flux B"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : rupture de canal Donchian(20) en régime {signal.direction} "
        f"(filtre MA200), entrée={signal.entry_price}, stop={signal.stop_price} "
        f"(Hypothèse #1, docs/HYPOTHESES.md)"
    )


def _has_active_hypothesis_signal_or_trade(db_path: str, asset: str) -> bool:
    return _has_active_signal_or_trade(db_path, asset, HYPOTHESIS_SOURCE)


def _generate_and_queue_signal(db_path, client, asset: str) -> None:
    _generic_generate_and_queue_signal(
        db_path, client, asset,
        source=HYPOTHESIS_SOURCE, resolution="HOUR", entry_fn=evaluate_entry,
        channel=_CHANNEL, hypothesis_label=_HYPOTHESIS_LABEL, describe_signal=_describe_signal,
    )


def run_trend_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 10) -> None:
    """Boucle continue du Flux B. Intervalle par défaut plus long que
    Station X (60s vs 30s) : les bougies horaires ne changent pas plus
    vite qu'une fois par heure, inutile de solliciter l'API aussi souvent
    que pour la détection de remplissage d'ordres limite.

    Délègue à technical_strategy_executor.run_technical_strategy_loop
    (voir docstring du module ci-dessus) avec les paramètres propres à
    l'Hypothèse #1 — le compte principal (mêmes credentials que Station
    X, voir docs/DECISIONS.md du 20/08/2026).

    `startup_offset_seconds=10` (24/08/2026, voir docs/DECISIONS.md) :
    valeur fixe distincte de `executor.run_executor_loop` (0s), pour
    échelonner les 6 process de production sur la même IP — voir
    docstring de `technical_strategy_executor.run_technical_strategy_loop`."""
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS_SOURCE,
        assets=HYPOTHESIS_ASSETS,
        resolution="HOUR",
        entry_fn=evaluate_entry,
        api_key=config.capital_api_key,
        identifier=config.capital_identifier,
        password=config.capital_api_password,
        account_id=config.capital_account_id,
        channel=_CHANNEL,
        process_name=_PROCESS_NAME,
        hypothesis_label=_HYPOTHESIS_LABEL,
        describe_signal=_describe_signal,
        interval_seconds=interval_seconds,
        startup_offset_seconds=startup_offset_seconds,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_trend_loop(app_config, db_path=app_config.db_path)
