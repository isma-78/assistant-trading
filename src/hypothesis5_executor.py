"""
hypothesis5_executor.py — Boucle autonome de l'Hypothèse #5.

**REFONTE L5, 29/08/2026** (voir docs/HYPOTHESES.md, pré-enregistrement
du 29/08/2026, et docs/DECISIONS.md) : le déclencheur V3 (régime
structurel + RSI(14)/50, `hypothesis5_strategy.py`) est REMPLACÉ par
compression → expansion sur largeur de Bollinger normalisée
(`hypothesis5_strategy_v2.evaluate_entry`). L'ancien module est
ARCHIVÉ (voir archive/hypothesis5_strategy.py). Source live et backtest
changent de `hypothesis5` à `hypothesis5_v2` — **aucune position H5 v1
ouverte au moment de ce changement** (vérifié, voir docs/DECISIONS.md),
malgré un process activement déployé sur le VPS depuis le 23/08/2026.

**Sortie CHANGÉE en 100% trailing** (Donchian(20), FIGÉ) — décision
explicite du pré-enregistrement ("si un edge existe, il vient de
l'asymétrie du payoff, que le split détruirait") : l'ancien mécanisme
TP1(1R)/TP2(2R)/reliquat trailing (§2.10) est abandonné pour L5
spécifiquement (H1-H4 le gardent). `hypothesis5_strategy_v2.
evaluate_entry` retourne un `TrendSignal` avec `tp1=tp2=None`.

**Aucune confirmation de régime croisée** — L5 n'a pas de notion de
régime externe à confirmer (compression est une propriété de l'actif
lui-même).

**Test d'information et taux d'échec de resserrement de stop
(points D et E du prompt du 29/08/2026)** : voir docs/DECISIONS.md pour
le résultat et le statut du garde-fou de retry — prérequis explicite
avant tout déploiement futur de H5 (100% trailing = la plus exposée au
bug de resserrement déjà mesuré, 5563 échecs).

Process indépendant, compte Capital.com démo dédié ("hypothèse 5").
Identifiants et statut de déploiement VPS inchangés par ce chantier —
voir point A (déploiement suspendu au déblocage du garde-fou de taille,
ET aux points D/E ci-dessus spécifiquement pour H5).

9 actifs (8 existants + CHFJPY) — voir docs/HYPOTHESES.md.
"""

import src.hypothesis5_strategy_v2 as _h5_mod
from src.executor import HYPOTHESIS5_V2_SOURCE
from src.hypothesis5_strategy_v2 import evaluate_entry
from src.hypothesis_params import apply_overrides, get_resolution_override
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS5_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

_CHANNEL = "hypothesis5_strategy"
_PROCESS_NAME = "hypothesis5_executor"
_HYPOTHESIS_LABEL = "Hypothèse #5"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : compression -> expansion (L5) {signal.direction}, "
        f"entrée={signal.entry_price}, stop={signal.stop_price}, sortie 100% trailing "
        f"(docs/HYPOTHESES.md)"
    )


def run_hypothesis5_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 50) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #5 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis5).

    `startup_offset_seconds=50` (24/08/2026, voir docs/DECISIONS.md) :
    échelonnement des 6 process de production sur la même IP.

    **Overrides du cycle d'évolution** : clé `"H5_v2"`, jamais `"H5"`
    (n'hérite d'aucun paramètre déjà tuné pour l'ancienne logique v1) —
    variables ajustées du pré-enregistrement (`COMPRESSION_PERCENTILE`,
    `COMPRESSION_DURATION`, `STOP_BUFFER_PCT`)."""
    apply_overrides(_h5_mod, "H5_v2", db_path, ["COMPRESSION_PERCENTILE", "COMPRESSION_DURATION", "STOP_BUFFER_PCT"])
    resolution = get_resolution_override(db_path, "H5_v2", "entree", "MINUTE_15")
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS5_V2_SOURCE,
        assets=HYPOTHESIS5_ASSETS,
        resolution=resolution,
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
        startup_offset_seconds=startup_offset_seconds,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis5_loop(app_config, db_path=app_config.db_path)
