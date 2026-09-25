"""
_diag_h2_fidelity.py — Diagnostic PONCTUEL, lecture seule (26/09/2026, voir
docs/DECISIONS.md) : pourquoi seulement 12% des signaux live H2 (ancien
combo) sont retrouvés par le backtest, contre 52-82% ailleurs — et est-ce
que la configuration par défaut actuelle est touchée ?

Évalue `hypothesis2_strategy_v2.evaluate_entry` à CHAQUE bougie HOUR close
de la fenêtre, SANS logique de trade (aucun blocage « un trade à la fois »),
sous deux jeux d'entrées :
  - « backtest » : 220 bougies closes par résolution (DEFAULT_LOOKBACK) ;
  - « live »     : 220 HOUR, mais 99 HOUR_4 et 99 DAY closes (le live en
                   demande 100 et retire celle en formation).
La bougie HOUR en formation, que le live utilise, n'est pas reconstituable
depuis des bougies closes : effet résiduel, commun aux 5 hypothèses.

Mesures, pour l'ancien combo ET la configuration par défaut :
  A. accord heure par heure entre les deux jeux d'entrées ;
  B. part des signaux live (table `signals`) retrouvés sans logique de
     trade (même sens, bougie de l'heure du signal ou la précédente) ;
  C. cycle de vie côté live : signaux émis par épisode continu.

**AVERTISSEMENT (26/09/2026)** : la mesure B de ce script (66%) est FAUSSE —
contredite par scripts/_diag_h2_consistency.py, qui rejoue les mêmes 169
signaux avec la même information et obtient 151/169 (89%) à H-1, 158/169
(93%) à H-1 ou H. Erreur d'implémentation de B non identifiée ; ne pas
réutiliser. Les mesures A et C restent valides (A recoupée indépendamment
par scripts/_diag_h2_forming_bar.py : mêmes 48 heures divergentes).
"""

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis2_strategy_v2 as h2
from src.backtest_engine import bar_from_raw

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
DB = ROOT / "data" / "assistant_trading.db"
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
START, END = "2026-08-30T00:00:00", "2026-09-25T18:00:00"
COMBO = {"EMA_PERIOD": 20, "RSI_THRESHOLD": 55.0, "N_TF": 3, "SCORE_THRESHOLD": 1.0}
DEFAULTS = {"EMA_PERIOD": 50, "RSI_THRESHOLD": 50.0, "N_TF": 2, "SCORE_THRESHOLD": 2 / 3}
DURATION = {"HOUR": timedelta(hours=1), "HOUR_4": timedelta(hours=4), "DAY": timedelta(days=1)}


@contextmanager
def params(values):
    saved = {k: getattr(h2, k) for k in values}
    for k, v in values.items():
        setattr(h2, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(h2, k, v)


def dt(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def load(asset, res):
    raw = json.loads((HIST / f"{asset}_{res}.json").read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None]
    return [(dt(b.time_utc), b.to_candle()) for b in bars]


def closed_before(series, moment, depth):
    """Les `depth` dernières bougies CLOSES à `moment` (début + durée <= moment)."""
    out = [c for t, c in series if t + series_duration(series) <= moment]
    return out[-depth:]


_durations = {}


def series_duration(series):
    return _durations[id(series)]


def scan(asset, own, h4, day, extra_depth):
    """{heure de début de bougie HOUR: sens ou None}, évaluation sans état."""
    result = {}
    own_times = [t for t, _ in own]
    for i, (t, _) in enumerate(own):
        if not (dt(START) <= t <= dt(END)) or i < 219:
            continue
        close_time = t + DURATION["HOUR"]
        window = [c for _, c in own[i - 219:i + 1]]
        w4 = closed_before(h4, close_time, extra_depth)
        wd = closed_before(day, close_time, extra_depth)
        signal = h2.evaluate_entry(asset, window, w4, wd)
        result[t] = signal.direction if signal else None
    return result


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    live = {}
    for r in conn.execute(
        "SELECT actif, sens, created_at FROM signals WHERE source = 'hypothesis2_v2' "
        "AND created_at >= ? AND created_at < '2026-09-25T18:14:16' ORDER BY created_at", (START,),
    ):
        t = dt(r["created_at"]).replace(minute=0, second=0, microsecond=0)
        live.setdefault(r["actif"], []).append((t, r["sens"]))

    for label, values in (("ANCIEN COMBO", COMBO), ("DÉFAUT (production)", DEFAULTS)):
        agree = total = diff_sig = 0
        found_bt = found_lv = n_live = 0
        with params(values):
            for asset in ASSETS:
                own = load(asset, "HOUR")
                h4 = [x for x in load(asset, "HOUR_4") if x[0] >= dt(START) - timedelta(days=60)]
                day = [x for x in load(asset, "DAY") if x[0] >= dt(START) - timedelta(days=340)]
                _durations[id(h4)], _durations[id(day)] = DURATION["HOUR_4"], DURATION["DAY"]
                bt = scan(asset, own, h4, day, 220)
                lv = scan(asset, own, h4, day, 99)
                for t in bt:
                    total += 1
                    agree += bt[t] == lv[t]
                    diff_sig += (bt[t] is not None or lv[t] is not None) and bt[t] != lv[t]
                if values is COMBO:
                    for hour, sens in sorted(set(live.get(asset, []))):
                        # Le live, à l'heure H, voit la bougie H en formation ; la
                        # dernière close est H-1 : on teste H-1 et H.
                        n_live += 1
                        cands = [hour - DURATION["HOUR"], hour]
                        found_bt += any(bt.get(c) == sens for c in cands)
                        found_lv += any(lv.get(c) == sens for c in cands)
        print(f"=== {label} ===")
        print(f"  A. accord heure par heure backtest(220) vs live(99) : {agree}/{total} = {100*agree/total:.1f}% "
              f"— heures où au moins un des deux signale et ils diffèrent : {diff_sig}")
        if values is COMBO:
            print(f"  B. signaux live (heures distinctes) retrouvés SANS logique de trade : "
                  f"entrées backtest {found_bt}/{n_live} = {100*found_bt/n_live:.0f}%, "
                  f"entrées live {found_lv}/{n_live} = {100*found_lv/n_live:.0f}%")

    rows = conn.execute(
        "SELECT s.actif, s.sens, s.created_at, t.statut, t.annulation_motif FROM signals s "
        "LEFT JOIN trades t ON t.signal_id = s.id WHERE s.source = 'hypothesis2_v2' "
        "AND s.created_at >= ? AND s.created_at < '2026-09-25T18:14:16' ORDER BY s.actif, s.created_at", (START,),
    ).fetchall()
    episodes, last = [], None
    for r in rows:
        t = dt(r["created_at"])
        if last and r["actif"] == last[0] and r["sens"] == last[1] and t - last[2] <= timedelta(hours=2):
            episodes[-1].append(r)
        else:
            episodes.append([r])
        last = (r["actif"], r["sens"], t)
    statuts = {}
    for r in rows:
        statuts[r["statut"] or "aucun trade"] = statuts.get(r["statut"] or "aucun trade", 0) + 1
    sizes = sorted(len(e) for e in episodes)
    print("=== C. cycle de vie live (ancien combo) ===")
    print(f"  {len(rows)} signaux émis, {len(episodes)} épisodes continus (même actif/sens, écart <= 2 h), "
          f"signaux par épisode : médiane {sizes[len(sizes)//2]}, max {sizes[-1]}")
    print(f"  devenir des signaux : {statuts}")


if __name__ == "__main__":
    main()
