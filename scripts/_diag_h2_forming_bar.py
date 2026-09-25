"""
_diag_h2_forming_bar.py — Diagnostic PONCTUEL, lecture seule (26/09/2026,
suite de scripts/_diag_h2_fidelity.py, voir docs/DECISIONS.md).

1. Pour chaque signal live H2 distinct (actif, sens, heure — premier de
   l'heure) de la période de l'ancien combo, reconstitue ce que la boucle
   live voyait à la minute exacte du signal : 219 bougies HOUR closes + la
   bougie HOUR EN FORMATION reconstituée depuis les bougies MINUTE_15 closes
   de l'heure en cours (approximation au quart d'heure près), + 99 HOUR_4 et
   99 DAY closes. Compare au même calcul sur bougies closes seulement.
   **Partie « bougie en formation » non exécutée le 26/09/2026** : les
   fichiers MINUTE_15 s'arrêtent au 28/08 et l'API plafonne la plage de
   dates des requêtes M15 sous 1000 bougies (`error.invalid.max.daterange`)
   — rafraîchissement échoué dès la première requête, aucun fichier
   modifié. Seule la partie « bougies closes » de la mesure 1 est valide.
2. Configuration par défaut : nombre d'heures-signal (bougies closes) sous
   entrées « backtest » (220) et « live » (99), pour rapporter les heures
   divergentes au nombre d'heures où un signal existe.
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
from src.market_data import Candle

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
DB = ROOT / "data" / "assistant_trading.db"
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
START, COMBO_END, END = "2026-08-30T00:00:00", "2026-09-25T18:14:16", "2026-09-25T18:00:00"
COMBO = {"EMA_PERIOD": 20, "RSI_THRESHOLD": 55.0, "N_TF": 3, "SCORE_THRESHOLD": 1.0}
DEFAULTS = {"EMA_PERIOD": 50, "RSI_THRESHOLD": 50.0, "N_TF": 2, "SCORE_THRESHOLD": 2 / 3}
H, M15, H4, D = timedelta(hours=1), timedelta(minutes=15), timedelta(hours=4), timedelta(days=1)


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


def load(asset, res, since):
    raw = json.loads((HIST / f"{asset}_{res}.json").read_text(encoding="utf-8"))
    out = []
    for p in raw:
        b = bar_from_raw(p)
        if b is not None and dt(b.time_utc) >= since:
            out.append((dt(b.time_utc), b.to_candle()))
    return out


def closed(series, duration, moment, depth):
    return [c for t, c in series if t + duration <= moment][-depth:]


def forming_bar(m15, hour_start, moment):
    parts = [c for t, c in m15 if t >= hour_start and t + M15 <= moment]
    if not parts:
        return None
    return Candle(time_utc=hour_start.strftime("%Y-%m-%dT%H:%M:%S"), open=parts[0].open,
                  high=max(c.high for c in parts), low=min(c.low for c in parts), close=parts[-1].close)


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    firsts = {}
    for r in conn.execute(
        "SELECT actif, sens, created_at FROM signals WHERE source = 'hypothesis2_v2' AND created_at >= ? "
        "AND created_at < ? ORDER BY created_at", (START, COMBO_END),
    ):
        t = dt(r["created_at"])
        firsts.setdefault((r["actif"], r["sens"], t.replace(minute=0, second=0)), t)

    since_h, since_4, since_d = dt(START) - timedelta(days=20), dt(START) - timedelta(days=60), dt(START) - timedelta(days=340)
    data = {a: (load(a, "HOUR", since_h), load(a, "MINUTE_15", dt(START) - timedelta(days=2)),
                load(a, "HOUR_4", since_4), load(a, "DAY", since_d)) for a in ASSETS}

    stats = {"total": 0, "forming": 0, "closed_only": 0, "no_m15": 0, "early": 0, "early_forming": 0}
    with params(COMBO):
        for (asset, sens, hour), moment in sorted(firsts.items()):
            own, m15, h4, day = data[asset]
            base = closed(own, H, hour, 219)
            w4, wd = closed(h4, H4, moment, 99), closed(day, D, moment, 99)
            stats["total"] += 1
            early = moment - hour < M15
            stats["early"] += early
            prev = h2.evaluate_entry(asset, closed(own, H, hour, 220), w4, wd)  # bougies closes seulement
            stats["closed_only"] += bool(prev and prev.direction == sens)
            partial = forming_bar(m15, hour, moment)
            if partial is None:
                stats["no_m15"] += 1
                continue
            sig = h2.evaluate_entry(asset, base + [partial], w4, wd)
            ok = bool(sig and sig.direction == sens)
            stats["forming"] += ok
    t = stats["total"]
    print("=== 1. Ancien combo — signaux live distincts (actif, sens, heure) :", t)
    print(f"  reproduits avec bougies CLOSES seulement : {stats['closed_only']}/{t} = {100*stats['closed_only']/t:.0f}%")
    print(f"  reproduits avec bougie EN FORMATION (M15) : {stats['forming']}/{t - stats['no_m15']} = "
          f"{100*stats['forming']/max(1, t - stats['no_m15']):.0f}% "
          f"(non testables faute de M15 close dans l'heure : {stats['no_m15']}, dont signaux < 15 min après l'heure : {stats['early']})")

    with params(DEFAULTS):
        counts = {"bt": 0, "lv": 0, "either": 0, "differ": 0}
        for asset in ASSETS:
            own, _m15, h4, day = data[asset]
            for i, (t0, _) in enumerate(own):
                if not (dt(START) <= t0 <= dt(END)) or i < 219:
                    continue
                close_time = t0 + H
                window = [c for _, c in own[i - 219:i + 1]]
                bt = h2.evaluate_entry(asset, window, closed(h4, H4, close_time, 220), closed(day, D, close_time, 220))
                lv = h2.evaluate_entry(asset, window, closed(h4, H4, close_time, 99), closed(day, D, close_time, 99))
                b, l = (bt.direction if bt else None), (lv.direction if lv else None)
                counts["bt"] += b is not None
                counts["lv"] += l is not None
                counts["either"] += (b is not None or l is not None)
                counts["differ"] += (b is not None or l is not None) and b != l
    print("=== 2. Configuration par défaut — heures-signal (bougies closes, 9 actifs, 30/08 -> 25/09) ===")
    print(f"  entrées backtest (220) : {counts['bt']} ; entrées live (99) : {counts['lv']} ; "
          f"au moins un des deux : {counts['either']} ; divergentes : {counts['differ']} "
          f"= {100*counts['differ']/max(1, counts['either']):.1f}% des heures-signal")


if __name__ == "__main__":
    main()
