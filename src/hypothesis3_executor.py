"""
hypothesis3_executor.py — Boucle autonome de l'Hypothèse #3
(docs/HYPOTHESES.md, 20/08/2026 : « identique à l'Hypothèse #1, seule
la résolution de bougie change »). MA(200) + Donchian(20) — EXACTEMENT
la même logique d'ENTRÉE que le Flux B (trend_strategy.evaluate_entry,
réutilisée telle quelle, jamais redupliquée), sur des bougies M15
(`MINUTE_15`) au lieu de H1.

**Sortie basculée le 23/08/2026** (décision explicite d'Ismaël, voir
docs/DECISIONS.md — va délibérément à l'encontre de l'isolation
"timeframe seule" documentée le 20/08/2026) : `hypothesis3_strategy.
evaluate_entry` (pas `trend_strategy.evaluate_entry` directement) ajoute
désormais TP1(1R)/TP2(2R) au signal, déclenchant le mécanisme §2.10
(TP1/TP2/trailing sur le reliquat) au lieu du trailing Donchian(20) pur.
H1 reste le seul témoin en trailing pur (`trend_executor.py`, jamais
touché).

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

**Couche session/multi-timeframe, 23/08/2026, révisée en fin de
journée** (décision explicite d'Ismaël, voir
docs/DECISIONS.md/docs/HYPOTHESES.md) : **Confirmation de régime croisée
AJOUTÉE** (`regime_confirmation.compute_index_regimes`/
`derive_confirmed_regime`, indices US30 ET US100 combinés) — la rupture
de canal Donchian (déclencheur INCHANGÉ) ne se traduit en signal
persisté que si le régime confirmé actuellement EN CACHE (rafraîchi aux
3 ouvertures de session UTC — 0h/8h/13h — plus une fois au démarrage du
process, voir `technical_strategy_executor.py`) concorde avec la
direction du trigger. La génération de signaux elle-même tourne en
CONTINU, à chaque cycle, tous les actifs — plus aucune fenêtre de
session ne la bloque (le premier gate du 23/08/2026 après-midi est
devenu obsolète et a été retiré le même jour, voir docs/DECISIONS.md).

8 actifs (les mêmes que l'Hypothèse #1) — voir docs/HYPOTHESES.md pour
le raisonnement (compte totalement séparé, aucun risque de collision).
"""

import src.hypothesis3_strategy as _h3_mod
from src.executor import HYPOTHESIS3_SOURCE
from src.hypothesis3_strategy import evaluate_entry
from src.hypothesis_params import apply_overrides, get_resolution_override
from src.technical_strategy_executor import run_technical_strategy_loop

HYPOTHESIS3_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

_CHANNEL = "hypothesis3_strategy"
_PROCESS_NAME = "hypothesis3_executor"
_HYPOTHESIS_LABEL = "Hypothèse #3"


def _describe_signal(hypothesis_label: str, asset: str, signal) -> str:
    return (
        f"{hypothesis_label} — {asset} : rupture de canal Donchian(20) en régime {signal.direction} "
        f"(filtre MA200, bougies M15), entrée={signal.entry_price}, stop={signal.stop_price}, "
        f"TP1={signal.tp1} (1R), TP2={signal.tp2} (2R), reliquat 20% sous trailing ATR "
        f"(docs/HYPOTHESES.md, docs/DECISIONS.md 23/08/2026)"
    )


def run_hypothesis3_loop(config, db_path: str, interval_seconds: int = 60, startup_offset_seconds: int = 30) -> None:
    """Délègue à technical_strategy_executor.run_technical_strategy_loop
    avec les paramètres propres à l'Hypothèse #3 : compte et identifiants
    Capital.com dédiés (config.capital_*_hypothesis3), résolution M15.

    `startup_offset_seconds=30` (24/08/2026, voir docs/DECISIONS.md) :
    échelonnement des 6 process de production sur la même IP, voir
    docstring de `technical_strategy_executor.run_technical_strategy_loop`.

    **Overrides du cycle d'évolution** (25/08/2026, voir
    docs/HYPOTHESES.md "cycle 2") : appliqués une seule fois ici, au
    démarrage — `apply_overrides` (TP1/TP2 de `hypothesis3_strategy`) et
    `get_resolution_override` (résolution d'entrée et de confirmation,
    indépendantes). Aucun effet tant qu'aucune ligne `rule_changes`
    "H3.*" avec `statut='applique'` n'existe (fail-safe, comportement
    actuel inchangé par défaut)."""
    apply_overrides(_h3_mod, "H3", db_path, ["TP1_R_MULTIPLE", "TP2_R_MULTIPLE"])
    resolution = get_resolution_override(db_path, "H3", "entree", "MINUTE_15")
    confirming_resolution = get_resolution_override(db_path, "H3", "confirmation", resolution)
    run_technical_strategy_loop(
        config, db_path,
        source=HYPOTHESIS3_SOURCE,
        assets=HYPOTHESIS3_ASSETS,
        resolution=resolution,
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
        require_regime_confirmation=True,
        startup_offset_seconds=startup_offset_seconds,
        confirming_resolution=confirming_resolution,
    )


if __name__ == "__main__":
    import logging as _logging

    from src.config import load_config
    from src.db import init_db as _init_db

    _logging.basicConfig(level=_logging.INFO)
    app_config = load_config()
    _init_db(app_config.db_path)
    run_hypothesis3_loop(app_config, db_path=app_config.db_path)
