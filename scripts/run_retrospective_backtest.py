"""
run_retrospective_backtest.py — Rejoue les 5 hypothèses sur l'historique
téléchargé (scripts/download_historical_data.py), persiste les résultats
dans les tables existantes (`signals`/`trades`/`market_snapshots`/
`envelopes`) sous des sources DÉDIÉES (`*_backtest`, jamais les sources
live), pour que `confidence_scorer.py`/`metrics.py` les lisent sans
modification. Méthodologie complète pré-enregistrée dans
docs/HYPOTHESES.md (24/08/2026, soir) avant ce script.

JAMAIS appelé depuis les 6 boucles live. Ne fait AUCUN appel réseau
(toutes les bougies viennent des fichiers JSON déjà téléchargés) — peut
tourner autant de fois que nécessaire sans aucun impact sur le
rate-limit Capital.com.

Usage :
    python scripts/run_retrospective_backtest.py [--hypothesis H1,H2,H3,H4,H5] [--assets GOLD,EURUSD]

Idempotence : PAS idempotent — ré-exécuter le script AJOUTE de nouveaux
signaux/trades (source `*_backtest`) à chaque appel, il ne remplace rien.
Pour recommencer proprement, supprimer manuellement les lignes
`source LIKE '%_backtest'` de `signals`/`trades`/`market_snapshots`/
`envelopes` avant de relancer (script ponctuel, pas d'automatisation de
purge — décision délibérée, éviter un DELETE accidentel sur des données
qui ne sont pas encore générées deux fois par erreur)."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import BacktestTrade, HistoricalBar, bar_from_raw, replay_hypothesis
from src.config import load_config
from src.db import connection_scope, init_db
from src.envelope_store import load_or_create_envelope
from src.executor import (
    HYPOTHESIS2_BACKTEST_SOURCE,
    HYPOTHESIS3_BACKTEST_SOURCE,
    HYPOTHESIS4_BACKTEST_SOURCE,
    HYPOTHESIS5_BACKTEST_SOURCE,
    HYPOTHESIS_BACKTEST_SOURCE,
)
from src.hypothesis3_strategy import evaluate_entry as h3_evaluate_entry
from src.hypothesis2_strategy import evaluate_entry as h2_evaluate_entry
from src.hypothesis5_strategy import evaluate_entry as h5_evaluate_entry
from src.mean_reversion_strategy import evaluate_entry as h4_evaluate_entry
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import evaluate_entry as h1_evaluate_entry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"

ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

# (label, source_backtest, entry_fn, resolution, require_regime_confirmation, is_donchian_trailing)
HYPOTHESES = {
    "H1": ("Hypothèse #1", HYPOTHESIS_BACKTEST_SOURCE, h1_evaluate_entry, "HOUR", False, True),
    "H2": ("Hypothèse #2", HYPOTHESIS2_BACKTEST_SOURCE, h2_evaluate_entry, "MINUTE_15", False, False),
    "H3": ("Hypothèse #3", HYPOTHESIS3_BACKTEST_SOURCE, h3_evaluate_entry, "MINUTE_15", True, False),
    "H4": ("Hypothèse #4", HYPOTHESIS4_BACKTEST_SOURCE, h4_evaluate_entry, "MINUTE_15", True, False),
    "H5": ("Hypothèse #5", HYPOTHESIS5_BACKTEST_SOURCE, h5_evaluate_entry, "MINUTE_15", False, False),
}

_bar_cache: Dict[str, List[HistoricalBar]] = {}

# Compteur monotone pour raw_messages.telegram_msg_id — évite toute
# collision UNIQUE(channel, telegram_msg_id) lors d'insertions rapprochées
# (contrairement à un horodatage seul, qui peut se répéter à la
# microseconde près sur des insertions en boucle serrée).
_synthetic_msg_id_base = int(datetime.now(timezone.utc).timestamp() * 1000)
_synthetic_msg_id_counter = [0]


def _next_synthetic_msg_id() -> int:
    _synthetic_msg_id_counter[0] += 1
    return _synthetic_msg_id_base + _synthetic_msg_id_counter[0]


def _load_bars(epic: str, resolution: str) -> List[HistoricalBar]:
    key = f"{epic}_{resolution}"
    if key in _bar_cache:
        return _bar_cache[key]
    path = HISTORICAL_DIR / f"{key}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} introuvable — lancer scripts/download_historical_data.py d'abord "
            f"(--assets {epic} --resolutions {resolution})"
        )
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[key] = bars
    return bars


def _persist_trade(db_path: str, asset: str, source: str, channel: str, trade: BacktestTrade) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, reply_to_msg_id, channel, received_at, raw_text, message_type, processed) "
            "VALUES (?, NULL, ?, ?, ?, 'signal', 1)",
            (
                _next_synthetic_msg_id(),
                channel, now,
                f"Backtest rétrospectif — {asset} {trade.direction} @ {trade.signal_time_utc} "
                f"(entrée signal={trade.entry_price_signal}, stop={trade.stop_price_signal})",
            ),
        )
        raw_message_id = raw_id.lastrowid

        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, type, actif, sens, entree_min, entree_max, stop_loss, "
            "tp1, tp2, take_profit, confiance, statut, created_at) "
            "VALUES (?, ?, 'signal', ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'approuve', ?)",
            (
                raw_message_id, source, asset, trade.direction,
                trade.entry_price_signal, trade.entry_price_signal, trade.stop_price_signal,
                trade.tp1, trade.tp2, trade.take_profit, trade.signal_time_utc,
            ),
        ).lastrowid

        conn.execute(
            "INSERT INTO trades (signal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, ferme_at, r_multiple_total, pnl_brut, pnl_net, statut) "
            "VALUES (?, ?, ?, 'demo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ferme')",
            (
                signal_id, source, asset, trade.direction, trade.units,
                trade.entry_price_signal, trade.entry_price_executed, trade.stop_price_signal, trade.stop_price_signal,
                trade.risk_amount_eur, 0.0,  # pourcentage_risque_applique non recalculé ici (informatif seulement)
                trade.entry_time_utc, trade.exit_time_utc, trade.r_multiple_total, trade.pnl_eur, trade.pnl_eur,
            ),
        )

        conn.execute(
            "INSERT INTO market_snapshots (signal_id, bid, ask, spread, captured_at) VALUES (?, ?, ?, ?, ?)",
            (signal_id, trade.bid_at_signal, trade.ask_at_signal, trade.spread_at_signal, trade.signal_time_utc),
        )


def run_one_hypothesis(
    db_path: str, key: str, assets: List[str], envelope_initial: float, confidence_threshold: float,
    slippage_multiplier: float = 1.0,
) -> None:
    label, source, entry_fn, resolution, require_regime, is_donchian = HYPOTHESES[key]
    caps = RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=envelope_initial)
    risk_engine = RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)

    print(f"=== {label} ({key}) — source={source}, résolution={resolution}, slippage_multiplier={slippage_multiplier} ===")
    for asset in assets:
        own_bars = _load_bars(asset, resolution)
        if len(own_bars) < 50:
            print(f"  {asset} : historique insuffisant ({len(own_bars)} bougies) — ignoré.")
            continue

        confirming_bars: Optional[Dict[str, List[HistoricalBar]]] = None
        if require_regime and asset not in ("US30", "US100"):
            confirming_bars = {"US30": _load_bars("US30", resolution), "US100": _load_bars("US100", resolution)}

        result = replay_hypothesis(
            asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, envelope_initial, confidence_threshold,
            require_regime_confirmation=require_regime, confirming_bars=confirming_bars, is_donchian_trailing=is_donchian,
            slippage_multiplier=slippage_multiplier,
        )
        envelope_id, _ = load_or_create_envelope(db_path, asset, "demo", envelope_initial, source=source)
        for trade in result.trades:
            _persist_trade(db_path, asset, source, channel=f"{source}_strategy", trade=trade)

        # Solde final de l'enveloppe backtest écrit directement (PAS via
        # envelope_store.persist_trade_result — celle-ci écrirait aussi
        # dans reserve_ledger, la réserve globale RÉELLE, jamais touchée
        # par le backtest, voir docs/HYPOTHESES.md). envelope_ledger
        # (mouvement par trade) n'est pas non plus alimenté ici — gap
        # assumé : seul metrics.get_trade_pnl_movements (dashboard "gains
        # par période") en dépendrait, confidence_scorer lit `trades`/
        # `envelopes.capital_courant` directement, non affecté.
        with connection_scope(db_path) as conn:
            conn.execute(
                "UPDATE envelopes SET capital_courant = ?, updated_at = ? WHERE id = ?",
                (result.final_envelope_balance, datetime.now(timezone.utc).isoformat(), envelope_id),
            )
        print(f"  {asset} : {len(result.trades)} trades simulés, enveloppe finale {result.final_envelope_balance:.2f}€")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hypothesis", default="H1,H2,H3,H4,H5")
    parser.add_argument("--assets", default=",".join(ALL_ASSETS))
    parser.add_argument(
        "--slippage-multiplier", type=float, default=1.0,
        help="Multiplicateur de slippage forfaitaire (fraction du spread observé, voir backtest_engine.py "
             "SLIPPAGE_SPREAD_MULTIPLIER) — permet de comparer plusieurs hypothèses de coût (24/08/2026, "
             "voir docs/DECISIONS.md). Pour comparer sans écraser un run précédent, rediriger aussi DB_PATH.",
    )
    args = parser.parse_args()

    keys = [k.strip().upper() for k in args.hypothesis.split(",") if k.strip()]
    assets = [a.strip() for a in args.assets.split(",") if a.strip()]

    config = load_config()
    init_db(config.db_path)
    print(f"Base de données ciblée : {config.db_path} (slippage_multiplier={args.slippage_multiplier})")

    for key in keys:
        if key not in HYPOTHESES:
            print(f"Hypothèse inconnue ignorée : {key}")
            continue
        run_one_hypothesis(
            config.db_path, key, assets, config.envelope_initial, config.confidence_threshold,
            slippage_multiplier=args.slippage_multiplier,
        )

    print("Terminé.")


if __name__ == "__main__":
    main()
