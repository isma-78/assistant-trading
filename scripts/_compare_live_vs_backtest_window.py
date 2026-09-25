"""
_compare_live_vs_backtest_window.py — Script PONCTUEL (préfixe _) : vérifie
qu'il n'existe pas de divergence entre la logique d'ENTRÉE exécutée en
direct (technical_strategy_executor.py) et celle SIMULÉE par
backtest_engine.replay_hypothesis, sur la fenêtre EXACTE des trades réels.

**Réparé une seconde fois le 25/09/2026 (soir), voir docs/DECISIONS.md** —
le diagnostic du même jour avait établi trois causes au « 0 trade backtest » :
1. les bougies historiques s'arrêtaient au 28-30/08, avant toute la fenêtre
   live -> désormais VÉRIFIÉ avant tout rejeu : couverture insuffisante =
   message explicite et code de sortie 1, jamais un « 0 trade » silencieux
   (données complétées par scripts/refresh_historical_tail.py) ;
2. la configuration rejouée n'était pas celle qui avait produit les trades
   -> chaque période de paramètres est rejouée avec SES paramètres
   (`PARAM_PERIODS`, historique explicite de `rule_changes`) ;
3. H1 et H5 n'étaient pas couvertes -> ajoutées.

Mesure produite : appariement des SIGNAUX (même sens, écart <= 2 h entre la
bougie de signal backtest et l'ouverture live), dédoublonnés à l'heure —
les ordres annulés comptent (un signal a bien été émis). Les R ne sont PAS
comparés avant E1 : la sortie live H1-H4 était un TP fixe involontaire
(clôture partielle ignorée par le broker), la sortie simulée non.

Limite connue : le filtre « heures chères » (H1/H3/H4/H5 en direct depuis
le 30/08) n'est pas répliqué par replay_hypothesis — un signal live absent
à ces heures-là peut apparaître comme « backtest seulement ».

Lecture seule (base + data/historical/, présent uniquement sur le VPS).
"""

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_mod
import src.hypothesis2_strategy_v2 as h2_mod
import src.hypothesis3_strategy_v2 as h3_mod
import src.hypothesis4_strategy_v2 as h4_mod
import src.hypothesis5_strategy_v2 as h5_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
DB_PATH = PROJECT_ROOT / "data" / "assistant_trading.db"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
MATCH_TOLERANCE = timedelta(hours=2)
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}

HYPOTHESES = {
    "hypothesis_v2": {"label": "H1", "module": h1_mod, "resolution": "HOUR", "multi_tf": False, "donchian": False},
    "hypothesis2_v2": {"label": "H2", "module": h2_mod, "resolution": "HOUR", "multi_tf": True, "donchian": False},
    "hypothesis3_v2": {"label": "H3", "module": h3_mod, "resolution": "HOUR", "multi_tf": False, "donchian": False},
    "hypothesis4_v2": {"label": "H4", "module": h4_mod, "resolution": "HOUR", "multi_tf": False, "donchian": False},
    "hypothesis5_v2": {"label": "H5", "module": h5_mod, "resolution": "HOUR", "multi_tf": False, "donchian": True},
}

# Historique des paramètres réellement actifs (rule_changes, vérifié le
# 25/09/2026 : seules les 4 lignes H2_v2 ont jamais été 'applique', du
# 30/08 au retrait du 25/09 18:14:16 UTC ; aucune autre hypothèse n'a eu
# d'override). (début inclus, fin exclue, attributs du module).
PARAM_PERIODS = {
    "hypothesis2_v2": [
        ("2026-08-30T00:00:00", "2026-09-25T18:14:16",
         {"EMA_PERIOD": 20, "RSI_THRESHOLD": 55.0, "N_TF": 3, "SCORE_THRESHOLD": 1.0}),
        ("2026-09-25T18:14:16", "2100-01-01T00:00:00", {}),
    ],
}
DEFAULT_PERIOD = [("2026-08-29T00:00:00", "2100-01-01T00:00:00", {})]


@contextmanager
def _override(module, attrs: Dict[str, object]):
    saved = {name: getattr(module, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


_cache: Dict[tuple, List[HistoricalBar]] = {}


def _bars(asset: str, resolution: str) -> List[HistoricalBar]:
    key = (asset, resolution)
    if key not in _cache:
        raw = json.loads((HISTORICAL_DIR / f"{asset}_{resolution}.json").read_text(encoding="utf-8"))
        _cache[key] = [b for b in (bar_from_raw(p) for p in raw) if b is not None]
    return _cache[key]


def _norm(iso_ts: str) -> str:
    return datetime.fromisoformat(iso_ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _dt(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def _live_signals(conn, source: str, start: str, end: str) -> Dict[str, List[tuple]]:
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at FROM trades WHERE source = ? ORDER BY ouvert_at", (source,),
    ).fetchall()
    by_asset: Dict[str, set] = {}
    for r in rows:
        t = _norm(r["ouvert_at"])
        if start <= t < end:
            by_asset.setdefault(r["actif"], set()).add((r["direction"], t[:13]))  # dédoublonné à l'heure
    return {a: sorted(v, key=lambda x: x[1]) for a, v in by_asset.items()}


def main() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    engine = RiskEngine(caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=ENVELOPE_INITIAL),
                        whitelist=ASSET_WHITELIST)
    failures = 0
    for source, cfg in HYPOTHESES.items():
        for start, end, attrs in PARAM_PERIODS.get(source, DEFAULT_PERIOD):
            live = _live_signals(conn, source, start, end)
            label = f"{cfg['label']} ({source}) période {start} -> {end[:10]} params={attrs or 'défaut'}"
            if not live:
                print(f"=== {label} : aucun trade réel ===\n")
                continue
            last_live = max(t for sigs in live.values() for _, t in sigs) + ":00:00"
            print(f"=== {label} : {sum(len(v) for v in live.values())} signaux live (dédoublonnés à l'heure) ===")
            tot_live = tot_bt = tot_match = 0
            for asset, signals in live.items():
                needed = {cfg["resolution"]: timedelta(hours=1)}
                if cfg["multi_tf"]:
                    needed.update({r: timedelta(seconds=2 * s) for r, s in EXTRA_SECONDS.items()})
                coverage = {res: _bars(asset, res)[-1].time_utc for res in needed}
                if any(_dt(coverage[res]) < _dt(last_live) - slack for res, slack in needed.items()):
                    print(f"  {asset}: COUVERTURE INSUFFISANTE — dernières bougies {coverage}, dernier signal live {last_live}")
                    failures += 1
                    continue
                own = [b for b in _bars(asset, cfg["resolution"]) if b.time_utc <= end]
                kwargs = {"is_donchian_trailing": cfg["donchian"]}
                if cfg["multi_tf"]:
                    kwargs.update(
                        extra_resolution_bars={r: [b for b in _bars(asset, r) if b.time_utc <= end] for r in ("HOUR_4", "DAY")},
                        own_bar_duration_seconds=3600.0, extra_resolution_seconds=EXTRA_SECONDS,
                    )
                with _override(cfg["module"], attrs):
                    result = replay_hypothesis(asset, own, cfg["module"].evaluate_entry, engine, ASSET_WHITELIST,
                                               ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD, **kwargs)
                first_live = _dt(signals[0][1] + ":00:00") - MATCH_TOLERANCE
                bt = [(t.direction, _dt(t.signal_time_utc)) for t in result.trades
                      if first_live <= _dt(t.signal_time_utc) < _dt(end) and t.signal_time_utc <= last_live]
                matched = sum(
                    1 for d, h in signals
                    if any(bd == d and abs(bt_time - _dt(h + ":00:00")) <= MATCH_TOLERANCE for bd, bt_time in bt)
                )
                tot_live, tot_bt, tot_match = tot_live + len(signals), tot_bt + len(bt), tot_match + matched
                print(f"  {asset}: live={len(signals)} backtest={len(bt)} appariés={matched}")
            if tot_live:
                print(f"  TOTAL : live={tot_live} backtest={tot_bt} appariés={tot_match} "
                      f"({100 * tot_match / tot_live:.0f}% des signaux live retrouvés)\n")
    conn.close()
    print("Terminé." if not failures else f"Terminé avec {failures} couverture(s) insuffisante(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
