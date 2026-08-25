"""
_compare_live_vs_backtest_window.py — Script PONCTUEL (préfixe _
volontaire, jamais commité comme partie du pipeline), pour la demande
d'Ismaël du 25/08/2026 : vérifier qu'il n'existe pas de divergence entre
la logique EXÉCUTÉE en direct (technical_strategy_executor.py /
executor.py) et celle SIMULÉE par backtest_engine.replay_hypothesis, en
comparant les trades produits par le backtest sur la fenêtre EXACTE des
trades réels déjà passés (pas les 2 ans complets) aux trades réellement
ouverts en direct sur cette même fenêtre.

Aucune écriture DB (lecture seule sur data/assistant_trading.db et les
fichiers JSON déjà téléchargés/complétés). Utilise la configuration
ACTUELLEMENT déployée en direct (aucun override actif — vérifié
séparément, voir docs/DECISIONS.md 25/08/2026), donc rejoue exactement
ce que les modules de stratégie feraient sur ces bougies.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis2_strategy as h2_mod
import src.hypothesis3_strategy as h3_mod
import src.mean_reversion_strategy as h4_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
DB_PATH = PROJECT_ROOT / "data" / "assistant_trading.db"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

# Marge de warm-up avant la fenêtre réelle, large pour couvrir MA200 (H2/H3/H4)
WARMUP_DAYS = 45

HYPOTHESES = {
    "hypothesis2": {"label": "H2", "module": h2_mod, "require_regime": False},
    "hypothesis3": {"label": "H3", "module": h3_mod, "require_regime": True},
    "hypothesis4": {"label": "H4", "module": h4_mod, "require_regime": True},
}


def _load_bars(epic: str) -> List[HistoricalBar]:
    path = HISTORICAL_DIR / f"{epic}_MINUTE_15.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _to_bar_format(iso_ts: str) -> str:
    """Normalise un horodatage trades.ouvert_at (avec offset +00:00 et
    microsecondes) au même format que HistoricalBar.time_utc
    ("YYYY-MM-DDTHH:MM:SS", toujours UTC, sans offset ni microsecondes) —
    comparaison lexicale directe sinon incorrecte entre les deux formats."""
    dt = datetime.fromisoformat(iso_ts).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _live_trades(conn: sqlite3.Connection, source: str) -> Dict[str, list]:
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, ferme_at, statut, pnl_net, r_multiple_total "
        "FROM trades WHERE source = ? ORDER BY actif, ouvert_at",
        (source,),
    ).fetchall()
    by_asset: Dict[str, list] = {}
    for r in rows:
        by_asset.setdefault(r["actif"], []).append(dict(r))
    return by_asset


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    all_bars_cache: Dict[str, List[HistoricalBar]] = {a: _load_bars(a) for a in ALL_ASSETS}

    for source, cfg in HYPOTHESES.items():
        live_by_asset = _live_trades(conn, source)
        if not live_by_asset:
            print(f"=== {cfg['label']} ({source}) : aucun trade réel — rien à comparer ===\n")
            continue

        all_open_times = [t["ouvert_at"] for trades in live_by_asset.values() for t in trades]
        window_start_norm = _to_bar_format(min(all_open_times))
        window_end_norm = _to_bar_format(max(all_open_times))
        print(f"=== {cfg['label']} ({source}) : fenêtre réelle {min(all_open_times)} -> {max(all_open_times)} ===")

        entry_fn = cfg["module"].evaluate_entry
        risk_engine = _make_risk_engine()

        for asset, live_trades in live_by_asset.items():
            own_bars_full = all_bars_cache[asset]
            # Filtre : tout jusqu'à la fin de fenêtre (le warm-up MA200/Donchian
            # utilise naturellement tout l'historique en amont, replay_hypothesis
            # ne produit de trades qu'une fois les indicateurs valides).
            own_bars = [b for b in own_bars_full if b.time_utc <= window_end_norm]
            confirming_bars = None
            if cfg["require_regime"]:
                confirming_bars = {
                    "US30": [b for b in all_bars_cache["US30"] if b.time_utc <= window_end_norm],
                    "US100": [b for b in all_bars_cache["US100"] if b.time_utc <= window_end_norm],
                }
            result = replay_hypothesis(
                asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
                require_regime_confirmation=cfg["require_regime"], confirming_bars=confirming_bars,
            )
            # Marge de 2h avant le premier trade réel (le signal backtest
            # peut précéder légèrement l'ouverture réelle : ordre limite,
            # cycle de 60s côté live) — jamais une comparaison stricte à
            # l'horodatage près, seulement à la fenêtre près.
            window_start_dt = datetime.strptime(window_start_norm, "%Y-%m-%dT%H:%M:%S")
            margin_start = (window_start_dt - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
            bt_trades_in_window = [t for t in result.trades if t.signal_time_utc >= margin_start]

            print(f"  --- {asset} ---")
            print(f"  RÉEL   ({len(live_trades)} trades) :")
            for t in live_trades:
                print(f"    {t['ouvert_at']} dir={t['direction']} statut={t['statut']} pnl_net={t['pnl_net']} r={t['r_multiple_total']}")
            print(f"  BACKTEST (même config, même fenêtre, {len(bt_trades_in_window)} trades) :")
            for t in bt_trades_in_window:
                print(f"    signal={t.signal_time_utc} entree={t.entry_time_utc} dir={t.direction} r={t.r_multiple_total:.4f} exit_reason={t.exit_reason}")
        print()

    conn.close()
    print("Terminé.")


if __name__ == "__main__":
    main()
