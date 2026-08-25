"""
hypothesis5_executor.py — Boucle autonome de l'Hypothèse #5 (régime
structurel + momentum RSI, sortie progressive §2.10), V3 depuis le
24/08/2026 (voir docs/HYPOTHESES.md/docs/DECISIONS.md — REMPLACE la
version "confluence ICT + RSI" validée le 23/08/2026, retirée après 0
signal en ~26h de production). Détection via hypothesis5_strategy.
evaluate_entry (délègue le régime structurel ET LA JAMBE D'IMPULSION,
SANS confluence Fibonacci/FVG, à ict_strategy.compute_structural_entry,
exige EN PLUS un franchissement du RSI(14) à 50 dans le même sens sur la
même bougie, ajoute TP1(1R)/TP2(2R)) — voir la docstring de
hypothesis5_strategy.py pour le détail complet de cette révision.

**Couche session/multi-timeframe, 23/08/2026** : résolution M15 (au lieu
de HOUR — le RSI(14)/MA200 internes à `hypothesis5_strategy`/
`ict_strategy` couvrent donc désormais ~3-4h/~50h de bougies au lieu de
~14h/~200h, conséquence mécanique du changement de résolution, pas un
nouveau paramètre). La fenêtre de session ne gate PLUS la génération de
signaux depuis la révision du 23/08/2026 fin de journée (voir
docs/DECISIONS.md/docs/HYPOTHESES.md — l'ancien gate + l'exemption
crypto associée sont devenus obsolètes et ont été retirés) : évaluation
continue, tous les actifs, à chaque cycle. **Aucune confirmation de
régime croisée** (option C — H5 déjà couverte par son propre régime
structurel BOS/CHoCH, voir docs/HYPOTHESES.md).

**Dépassement du budget §2.11 EXPLICITEMENT ASSUMÉ par Ismaël** : H5
était déjà à 3/3 paramètres (config RSI, TP1 R, TP2 R) — le changement
de résolution M15 porte le total à 4/3, au-delà du plafond 2-3.
Maintenu en connaissance de cause après mise en garde (voir
docs/DECISIONS.md, même précédent que le dépassement déjà accepté du
plafond §3.9 pour H4/H5) — pas un oubli.

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

import src.hypothesis5_strategy as _h5_mod
from src.executor import HYPOTHESIS5_SOURCE
from src.hypothesis5_strategy import evaluate_entry
from src.hypothesis_params import apply_overrides, get_resolution_override
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS5_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

_CHANNEL = "hypothesis5_strategy"
_PROCESS_NAME = "hypothesis5_executor"
_HYPOTHESIS_LABEL = "Hypothèse #5"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : régime structurel (BOS/CHoCH) ET franchissement "
        f"RSI(14)/50 dans le même sens (V3, 24/08/2026 — plus de confluence ICT, voir "
        f"docs/HYPOTHESES.md), direction {signal.direction}, entrée={signal.entry_price}, "
        f"stop={signal.stop_price}, TP1={signal.tp1} (1R), TP2={signal.tp2} (2R), "
        f"reliquat 20% sous trailing ATR"
    )


def run_hypothesis5_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 50) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #5 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis5 — voir docstring du
    module pour l'état actuel de ce prérequis).

    `startup_offset_seconds=50` (24/08/2026, voir docs/DECISIONS.md) :
    échelonnement des 6 process de production sur la même IP, voir
    docstring de `technical_strategy_executor.run_technical_strategy_loop`.

    **Overrides du cycle d'évolution** (25/08/2026, voir
    docs/HYPOTHESES.md "cycle 2") : mêmes principes que H3 (voir sa
    docstring) — RSI_PERIOD/TP1/TP2 via `apply_overrides`, résolution via
    `get_resolution_override`. H5 n'a pas de confirmation croisée
    (option C du 23/08/2026), aucun paramètre `confirming_resolution`
    ici."""
    apply_overrides(_h5_mod, "H5", db_path, ["RSI_PERIOD", "TP1_R_MULTIPLE", "TP2_R_MULTIPLE"])
    resolution = get_resolution_override(db_path, "H5", "entree", "MINUTE_15")
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS5_SOURCE,
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
