"""
hypothesis4_executor.py — Boucle autonome de l'Hypothèse #4 (retour à la
moyenne, Bandes de Bollinger), VALIDÉE par Ismaël le 21/08/2026 en démo
(voir docs/HYPOTHESES.md et docs/DECISIONS.md — le plafond §3.9 "3
hypothèses par cycle" ne bloque pas l'exécution démo sans capital réel ;
toute future évaluation/validation de H1/H2/H3/H4 appliquera néanmoins la
correction pour comparaisons multiples calibrée sur 4 hypothèses, jamais 3).

Détection via mean_reversion_strategy.evaluate_entry (régime MA(200) +
toucher de bande de Bollinger(20, 2σ) opposée au régime), déclencheur
INCHANGÉ par tout ce qui suit.

**Couche session/multi-timeframe, 23/08/2026, révisée en fin de
journée** (décision explicite d'Ismaël, voir
docs/DECISIONS.md/docs/HYPOTHESES.md) : résolution M15 (au lieu de
HOUR) + confirmation de régime croisée (`regime_confirmation.
compute_index_regimes`/`derive_confirmed_regime`, indices US30 ET US100
combinés) : le toucher de bande Bollinger (déclencheur INCHANGÉ) ne se
traduit en signal persisté que si le régime confirmé actuellement EN
CACHE (rafraîchi aux 3 ouvertures de session UTC — 0h/8h/13h — plus une
fois au démarrage du process, voir `technical_strategy_executor.py`)
concorde avec la direction du trigger — H4 est "contre-tendance" par
construction (fade d'une extension court terme À L'INTÉRIEUR d'un
régime de fond confirmé, jamais contre lui, voir docs/HYPOTHESES.md).
La génération de signaux elle-même tourne en CONTINU, à chaque cycle,
tous les actifs — plus aucune fenêtre de session ne la bloque (le
premier gate du 23/08/2026 après-midi est devenu obsolète et a été
retiré le même jour, voir docs/DECISIONS.md).

**3e mécanisme de sortie**, distinct de Station X (TP1/TP2/TP3 + trailing
ATR) et du Flux B/H3/H2 (aucun TP, trailing Donchian perpétuel) : TP fixe
unique (bande médiane figée à l'entrée), stop fixe, AUCUN trailing —
géré par la nouvelle branche `ManagementActionType.CLOSE_FULL_TP` de
`executor._evaluate_position_management` (voir docs/DECISIONS.md,
21/08/2026). `technical_strategy_executor._generate_and_queue_signal`
persiste `signal.take_profit` dans la colonne dédiée `signals.take_profit`,
jamais dans tp1/tp2 (qui resteraient sinon interprétés à tort comme un
signal Station X par ce dispatch).

Process indépendant, compte Capital.com démo dédié ("hypothèse 4").
**Identifiants Capital.com dédiés non fournis à ce jour** — Ismaël les
donnera directement (jamais dans la conversation, même principe que
H2/H3, voir CLAUDE.md sur la manipulation des secrets). `run_hypothesis4_
loop` échoue net (ConfigError, fail-safe, invariant #7) tant que
`CAPITAL_API_KEY_HYPOTHESIS4`/`CAPITAL_IDENTIFIER_HYPOTHESIS4`/
`CAPITAL_API_PASSWORD_HYPOTHESIS4`/`CAPITAL_ACCOUNT_ID_HYPOTHESIS4` ne
sont pas renseignés — jamais un repli silencieux vers un autre jeu
d'identifiants. **Ce module n'est PAS démarré sur le VPS** (pas ajouté à
scripts/process_watchdog.py — l'ajouter aurait déclenché une fausse
alerte, même précédent que hypothesis2_executor avant ses identifiants).

8 actifs (les mêmes que les Hypothèses #1, #2 et #3) — voir
docs/HYPOTHESES.md.
"""

from src.executor import HYPOTHESIS4_SOURCE
from src.mean_reversion_strategy import evaluate_entry
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS4_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

_CHANNEL = "hypothesis4_strategy"
_PROCESS_NAME = "hypothesis4_executor"
_HYPOTHESIS_LABEL = "Hypothèse #4"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : retour à la moyenne (Bandes de Bollinger 20, 2σ) "
        f"en régime {signal.direction} (filtre MA200), entrée={signal.entry_price}, "
        f"stop={signal.stop_price}, TP fixe={signal.take_profit} (docs/HYPOTHESES.md)"
    )


def run_hypothesis4_loop(config, db_path: str, interval_seconds: int = 60) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #4 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis4 — voir docstring du
    module pour l'état actuel de ce prérequis)."""
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS4_SOURCE,
        assets=HYPOTHESIS4_ASSETS,
        resolution="MINUTE_15",
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
        require_regime_confirmation=True,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis4_loop(app_config, db_path=app_config.db_path)
