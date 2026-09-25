"""
_compare_live_vs_backtest_window.py — Script PONCTUEL (préfixe _
volontaire, jamais commité comme partie du pipeline), pour la demande
d'Ismaël du 25/08/2026 : vérifier qu'il n'existe pas de divergence entre
la logique EXÉCUTÉE en direct (technical_strategy_executor.py /
executor.py) et celle SIMULÉE par backtest_engine.replay_hypothesis, en
comparant les trades produits par le backtest sur la fenêtre EXACTE des
trades réels déjà passés (pas les 2 ans complets) aux trades réellement
ouverts en direct sur cette même fenêtre.

**Réparé le 25/09/2026** (voir docs/DECISIONS.md) : cassé depuis le
29/08/2026 (`ModuleNotFoundError: src.hypothesis2_strategy`, module
archivé par la REFONTE L2/L3/L4 du même jour). Mis à jour pour suivre
les modules/sources/résolutions RÉELLEMENT déployés aujourd'hui,
lus directement dans hypothesis{2,3,4}_executor.py plutôt que supposés :
- H2 → `hypothesis2_strategy_v2` (source `hypothesis2_v2`), multi-TF
  HOUR (natif) + HOUR_4 + DAY, `require_regime_confirmation=False`.
- H3 → `hypothesis3_strategy_v2` (source `hypothesis3_v2`), mono-TF
  HOUR, `require_regime_confirmation=False` (changé depuis l'ancien H3).
- H4 → `hypothesis4_strategy_v2` (source `hypothesis4_v2`), mono-TF
  HOUR, `require_regime_confirmation=False` (changé depuis l'ancien H4
  mean-reversion).
Résolutions vérifiées sans override actif en base au 25/09/2026
(`rule_changes.variable LIKE '%resolution%'` vide) — si un override
`H3_v2.resolution_entree`/`H4_v2.resolution_entree` est appliqué plus
tard, l'ajouter ici via `hypothesis_params.get_resolution_override`
plutôt que de re-coder "HOUR" en dur (même risque de dérive silencieuse
qui a cassé ce script une première fois).

**Limite connue, non corrigée ici** : le filtre "heures chères"
(`spread_analysis.compute_expensive_hours_by_asset`, actif en direct
pour H3/H4 depuis le 30/08/2026) n'est pas répliqué — `replay_hypothesis`
ne le supporte pas. Un signal live supprimé par ce filtre peut donc
apparaître à tort comme "backtest seulement" dans la comparaison.

Aucune écriture DB (lecture seule sur data/assistant_trading.db et les
fichiers JSON déjà téléchargés/complétés dans data/historical/, absents
du dépôt local — script à exécuter sur le VPS). Utilise la configuration
ACTUELLEMENT déployée en direct (overrides actifs relus depuis
`rule_changes` via `hypothesis_params`, jamais supposés stables), donc
rejoue exactement ce que les modules de stratégie feraient sur ces
bougies.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis2_strategy_v2 as h2_mod
import src.hypothesis3_strategy_v2 as h3_mod
import src.hypothesis4_strategy_v2 as h4_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.hypothesis_params import apply_overrides, get_resolution_override
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
DB_PATH = PROJECT_ROOT / "data" / "assistant_trading.db"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

_OWN_BAR_SECONDS = {"HOUR": 3600.0, "MINUTE_15": 900.0}

apply_overrides(h2_mod, "H2_v2", str(DB_PATH), ["EMA_PERIOD", "RSI_THRESHOLD", "N_TF", "SCORE_THRESHOLD"])
apply_overrides(h3_mod, "H3_v2", str(DB_PATH), ["RETRACEMENT_RATIO", "CONFIRMATION_BARS", "STOP_BUFFER_ATR"])
apply_overrides(h4_mod, "H4_v2", str(DB_PATH), ["PIVOT_FRACTAL_N", "MAX_PIVOT_DISTANCE_BARS", "STOP_ATR_MULT"])

_H3_RESOLUTION = get_resolution_override(str(DB_PATH), "H3_v2", "entree", "HOUR")
_H4_RESOLUTION = get_resolution_override(str(DB_PATH), "H4_v2", "entree", "HOUR")

HYPOTHESES = {
    "hypothesis2_v2": {"label": "H2", "module": h2_mod, "require_regime": False, "resolution": "HOUR", "multi_tf": True},
    "hypothesis3_v2": {"label": "H3", "module": h3_mod, "require_regime": False, "resolution": _H3_RESOLUTION, "multi_tf": False},
    "hypothesis4_v2": {"label": "H4", "module": h4_mod, "require_regime": False, "resolution": _H4_RESOLUTION, "multi_tf": False},
}


def _load_bars(epic: str, resolution: str) -> List[HistoricalBar]:
    path = HISTORICAL_DIR / f"{epic}_{resolution}.json"
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

    bars_cache: Dict[str, Dict[str, List[HistoricalBar]]] = {}

    def _bars_for(asset: str, resolution: str) -> List[HistoricalBar]:
        by_res = bars_cache.setdefault(asset, {})
        if resolution not in by_res:
            by_res[resolution] = _load_bars(asset, resolution)
        return by_res[resolution]

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
        resolution = cfg["resolution"]

        for asset, live_trades in live_by_asset.items():
            # Filtre : tout jusqu'à la fin de fenêtre (le warm-up MA200/Donchian
            # utilise naturellement tout l'historique en amont, replay_hypothesis
            # ne produit de trades qu'une fois les indicateurs valides).
            own_bars = [b for b in _bars_for(asset, resolution) if b.time_utc <= window_end_norm]
            extra_kwargs = {}
            if cfg["multi_tf"]:
                hour4 = [b for b in _bars_for(asset, "HOUR_4") if b.time_utc <= window_end_norm]
                day = [b for b in _bars_for(asset, "DAY") if b.time_utc <= window_end_norm]
                extra_kwargs = {
                    "extra_resolution_bars": {"HOUR_4": hour4, "DAY": day},
                    "own_bar_duration_seconds": _OWN_BAR_SECONDS.get(resolution, 3600.0),
                    "extra_resolution_seconds": {"HOUR_4": 14400.0, "DAY": 86400.0},
                }
            result = replay_hypothesis(
                asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
                require_regime_confirmation=cfg["require_regime"], confirming_bars=None,
                **extra_kwargs,
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
