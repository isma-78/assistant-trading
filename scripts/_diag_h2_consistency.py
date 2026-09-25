"""
_diag_h2_consistency.py — Contrôle PONCTUEL (26/09/2026) : les deux
diagnostics H2 du même jour donnent 66% et 89% de signaux live reproduits
alors qu'ils devraient évaluer la même information à l'heure H-1. Rejoue les
169 signaux avec les deux méthodes côte à côte et affiche les écarts.
Lecture seule.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts._diag_h2_forming_bar as fb
import src.hypothesis2_strategy_v2 as h2


def main():
    conn = fb.sqlite3.connect(str(fb.DB))
    conn.row_factory = fb.sqlite3.Row
    firsts = {}
    for r in conn.execute(
        "SELECT actif, sens, created_at FROM signals WHERE source = 'hypothesis2_v2' AND created_at >= ? "
        "AND created_at < ? ORDER BY created_at", (fb.START, fb.COMBO_END),
    ):
        t = fb.dt(r["created_at"])
        firsts.setdefault((r["actif"], r["sens"], t.replace(minute=0, second=0)), t)
    since = fb.dt(fb.START)
    data = {a: (fb.load(a, "HOUR", since - fb.timedelta(days=20)), fb.load(a, "HOUR_4", since - fb.timedelta(days=60)),
                fb.load(a, "DAY", since - fb.timedelta(days=340))) for a in fb.ASSETS}
    counts = {"m1_prev": 0, "m1_curr": 0, "m1_any": 0, "m2": 0}
    examples = []
    with fb.params(fb.COMBO):
        for (asset, sens, hour), moment in sorted(firsts.items()):
            own, h4, day = data[asset]
            # Méthode 2 (forming_bar) : tout ce qui est clos à la minute du signal.
            m2 = h2.evaluate_entry(asset, fb.closed(own, fb.H, hour, 220),
                                   fb.closed(h4, fb.H4, moment, 99), fb.closed(day, fb.D, moment, 99))
            # Méthode 1 (fidelity) : bougie c = H-1 puis c = H, extras clos à la fin de c.
            res = {}
            for label, c in (("prev", hour - fb.H), ("curr", hour)):
                close_time = c + fb.H
                res[label] = h2.evaluate_entry(asset, fb.closed(own, fb.H, close_time, 220),
                                               fb.closed(h4, fb.H4, close_time, 99), fb.closed(day, fb.D, close_time, 99))
            ok2 = bool(m2 and m2.direction == sens)
            okp = bool(res["prev"] and res["prev"].direction == sens)
            okc = bool(res["curr"] and res["curr"].direction == sens)
            counts["m2"] += ok2
            counts["m1_prev"] += okp
            counts["m1_curr"] += okc
            counts["m1_any"] += okp or okc
            if ok2 != okp and len(examples) < 5:
                examples.append((asset, sens, hour, moment, ok2, okp))
    print("n =", len(firsts), counts)
    for e in examples:
        print("  écart :", e)


if __name__ == "__main__":
    main()
