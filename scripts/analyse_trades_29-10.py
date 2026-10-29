"""
analyse_trades_29-10.py — fiches de trades, causes gains/pertes, matrice
hypothèse × actif (docs/adequation_paires_2026-10-29.md). LECTURE SEULE
(base en mode=ro, bougies locales). Aucun appel broker, aucun LLM.

Usage : python -I scripts/analyse_trades_29-10.py <snapshot.db> <bougies_dir> <structural.json> <out_dir>
"""
import bisect
import csv
import json
import random
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HYP_OF = {"hypothesis": "H1", "hypothesis_v2": "H1", "hypothesis2": "H2", "hypothesis2_v2": "H2",
          "hypothesis3": "H3", "hypothesis3_v2": "H3", "hypothesis4": "H4", "hypothesis4_v2": "H4",
          "hypothesis5": "H5", "hypothesis5_v2": "H5", "-1002481537588": "SX"}
CHFJPY_EXCLUDED = {"H2", "H3", "H4", "H5"}
E1_START = "2026-09-25T19:12"
SEED, REPS = 20261029, 10000
SLIP_ADVERSE_R, COST_R_BAD = 0.10, 0.10


def ts(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def load_candles(hist, asset):
    p = Path(hist) / f"{asset}_HOUR.json"
    if not p.exists():
        return [], []
    rows = []
    for r in json.loads(p.read_text(encoding="utf-8")):
        try:
            t = datetime.fromisoformat(r["snapshotTimeUTC"])
            rows.append((t, r["openPrice"]["bid"], r["openPrice"]["ask"], r["highPrice"]["bid"], r["highPrice"]["ask"],
                         r["lowPrice"]["bid"], r["lowPrice"]["ask"], r["closePrice"]["bid"], r["closePrice"]["ask"]))
        except (KeyError, ValueError, TypeError):
            continue
    rows.sort()
    return [r[0] for r in rows], rows


def wilder_atr(rows, i, period=14):
    """ATR(14) de Wilder sur mid, barres < i (déjà closes)."""
    if i < period * 4:
        return None
    mid = lambda r, k: (r[k] + r[k + 1]) / 2
    trs = []
    for j in range(i - period * 4, i):
        h, l, pc = mid(rows[j], 3), mid(rows[j], 5), mid(rows[j - 1], 7)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def path_stats(cache, asset, direction, entry, stop_dist, t0, t1):
    """MAE/MFE en R sur [t0,t1] (barres horaires bid/ask) + continuation 24 h
    après t1. None si les bougies ne couvrent pas la période."""
    times, rows = cache[asset]
    if not times or times[-1] < t1:
        return None
    i0 = bisect.bisect_right(times, t0) - 1
    i1 = bisect.bisect_right(times, t1) - 1
    if i0 < 0 or i1 < i0 or stop_dist <= 0:
        return None
    seg = rows[i0:i1 + 1]
    if direction == "long":
        mae = (entry - min(r[5] for r in seg)) / stop_dist
        mfe = (max(r[3] for r in seg) - entry) / stop_dist
    else:
        mae = (max(r[4] for r in seg) - entry) / stop_dist
        mfe = (entry - min(r[6] for r in seg)) / stop_dist
    after = rows[i1 + 1:i1 + 25]
    cont = None
    if len(after) >= 12:
        cont = ((max(r[3] for r in after) - entry) if direction == "long" else (entry - min(r[6] for r in after))) / stop_dist
    return max(mae, 0), max(mfe, 0), cont


def boot_ci(v, rng):
    if len(v) < 2:
        return None, None
    m = sorted(statistics.fmean(rng.choices(v, k=len(v))) for _ in range(2000))
    return m[int(0.025 * len(m))], m[int(0.975 * len(m))]


def p_neg(v, rng):
    """p unilatéral H0 : moyenne >= 0 (bootstrap centré, borne inférieure)."""
    n, m = len(v), statistics.fmean(v)
    c = [x - m for x in v]
    cnt = sum(1 for _ in range(REPS) if statistics.fmean(rng.choices(c, k=n)) <= m)
    return (cnt + 1) / (REPS + 1)


def holm(pvals):
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    out, prev, m = {}, 0.0, len(items)
    for k, (key, p) in enumerate(items):
        adj = min(1.0, max(prev, (m - k) * p))
        out[key], prev = adj, adj
    return out


def main(db, hist, struct_path, out):
    out = Path(out)
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    struct = json.loads(Path(struct_path).read_text(encoding="utf-8"))["table"]
    assets = sorted({r[0] for r in c.execute("select distinct actif from trades")})
    cache = {a: load_candles(hist, a) for a in assets}
    snaps = {r["signal_id"]: r for r in c.execute("select signal_id, spread from market_snapshots")}
    parts = {}
    for r in c.execute("select trade_id, palier, fraction, r_atteint, prix_sortie_reel from trade_partials"):
        parts.setdefault(r["trade_id"], []).append(r)

    rows = c.execute("select * from trades where source not like '%backtest' and source != 'test_manuel_p2' "
                     "and statut in ('ferme','ferme_non_reconcilie','ouvert') order by ouvert_at").fetchall()
    fiches, excluded = [], {}
    for t in rows:
        src, hyp, a = t["source"], HYP_OF.get(t["source"]), t["actif"]
        if not src.endswith("_v2"):
            era = "v1"
        elif t["ouvert_at"] < E1_START:
            era = "v2_pre_E1"
        else:
            era = "E1"
        reason = None
        if t["statut"] == "ouvert":
            reason = "encore ouvert"
        elif t["statut"] == "ferme_non_reconcilie":
            reason = "fermé non réconcilié (aucun R)"
        elif t["r_multiple_total"] is None:
            reason = "R absent"
        elif t["anomalie_technique"]:
            reason = "anomalie technique connue"
        elif hyp in CHFJPY_EXCLUDED and a == "CHFJPY":
            reason = "CHFJPY exclue (pré-enreg. 29/08)"
        pr = parts.get(t["id"], [])
        strict = bool(pr) and all(p["prix_sortie_reel"] is not None for p in pr)
        if reason is None and era == "E1" and not strict:
            reason = "sortie sans prix broker"
        if reason:
            excluded.setdefault(reason, []).append(t["id"])
        direction, ent = t["direction"], t["prix_entree_reel"]
        prev, stop = t["prix_entree_prevu"], t["stop_loss_initial"]
        sd = abs(ent - stop) if ent and stop else None
        adverse = None
        if ent and prev and sd:
            adverse = ((ent - prev) if direction == "long" else (prev - ent)) / sd
        sn = snaps.get(t["signal_id"])
        spread = sn["spread"] if sn else None
        cost_r = spread / sd if (spread is not None and sd) else None
        t_open = ts(t["ouvert_at"])
        t_close = ts(t["ferme_at"]) if t["ferme_at"] else None
        atr = None
        times, crow = cache.get(a, ([], []))
        if times and t_open < times[-1]:
            i = bisect.bisect_right(times, t_open) - 1
            atr = wilder_atr(crow, i) if i >= 0 else None
        ps = path_stats(cache, a, direction, ent, sd, t_open, t_close) if (t_close and sd and ent and t["statut"] == "ferme") else None
        entry_s = struct.get(hyp, {}).get(a, {}) if hyp in struct else {}
        status = entry_s.get("status")
        vetos = (entry_s.get("veto_old") or []) + (entry_s.get("veto_recent") or [])
        veto_cost = any(v.startswith("C=") for v in vetos)
        veto_behav = any(v[:2] in ("P=", "V=") for v in vetos)
        fiches.append({
            "id": t["id"], "hyp": hyp, "source": src, "era": era, "actif": a, "direction": direction,
            "session": t["session_marche"], "heure_utc": t_open.hour, "ouvert_at": t["ouvert_at"][:19], "ferme_at": (t["ferme_at"] or "")[:19],
            "spread_entree": spread, "stop_dist": sd, "cout_spread_R": cost_r, "atr_h1": atr,
            "spread_sur_atr": (spread / atr) if (spread is not None and atr) else None,
            "R": t["r_multiple_total"], "MAE_R": ps[0] if ps else None, "MFE_R": ps[1] if ps else None,
            "continuation24h_R": ps[2] if ps else None,
            "duree_h": ((t_close - t_open).total_seconds() / 3600) if t_close else None,
            "motif_sortie": t["cloture_reason"], "type_sortie": t["exit_type"],
            "tp1_atteint": any(p["palier"] == "tp1" for p in pr),
            "type_ordre": "limite" + ("+stop_garanti" if t["guaranteed_stop"] else ""), "slippage_adverse_R": adverse,
            "statut_structurel": status, "veto_cout": veto_cost, "veto_comportement": veto_behav, "retenu": reason is None, "motif_exclusion": reason or "",
        })

    # ---- non-remplissages et signaux manqués (ère E1) ----
    miss = {"ordres_non_remplis": [], "signaux_rejetes_par_cause": []}
    for r in c.execute("select source, actif, annulation_motif, count(*) n from trades where statut='annule' and ouvert_at>=? "
                       "and source like '%_v2' group by 1,2,3", (E1_START,)):
        miss["ordres_non_remplis"].append([r["source"], r["actif"], r["annulation_motif"], r["n"]])
    for r in c.execute("select s.source, d.reason, count(*) n from signals s join risk_decisions d on d.signal_id=s.id "
                       "where s.source like '%_v2' and s.created_at>=? and d.approved=0 group by 1,2", (E1_START,)):
        miss["signaux_rejetes_par_cause"].append([r["source"], r["reason"], r["n"]])

    # ---- causes (règles déterministes, priorité d > b > c > e > a > f) ----
    for f in fiches:
        f["cause"] = ""
        if not f["retenu"] or f["R"] is None:
            continue
        if f["R"] > 0:
            if f["slippage_adverse_R"] is not None and f["slippage_adverse_R"] <= -SLIP_ADVERSE_R:
                f["cause"] = "G: bon timing d'exécution"
            elif f["R"] < 0.3:
                f["cause"] = "G: chance (gain marginal)"
            elif f["type_sortie"] == "trailing_pur" or f["motif_sortie"] == "trailing" or f["tp1_atteint"]:
                f["cause"] = "G: tendance conforme à l'hypothèse"
            else:
                f["cause"] = "G: bon rapport spread/stop" if (f["cout_spread_R"] or 1) <= 0.03 else "G: chance (gain marginal)"
        else:
            motif = f["motif_sortie"] or ""
            if motif == "stop_urgence" or (f["slippage_adverse_R"] or 0) >= SLIP_ADVERSE_R:
                f["cause"] = "P(d): exécution défaillante"
            elif f["veto_cout"] or (f["cout_spread_R"] or 0) >= COST_R_BAD:
                f["cause"] = "P(b): actif inadapté (coût spread/ATR)"
            elif f["session"] == "hors_session":
                f["cause"] = "P(c): mauvaise session"
            elif motif == "stop_initial" and (f["continuation24h_R"] or 0) >= 1.0:
                f["cause"] = "P(e): stop trop serré / sortie prématurée"
            elif f["veto_comportement"]:
                f["cause"] = "P(a): hypothèse non applicable à l'actif (persistance)"
            elif motif == "stop_initial" and not f["tp1_atteint"] and f["MFE_R"] is not None and f["MFE_R"] < 0.3:
                f["cause"] = "P(a): hypothèse fausse (jamais validée par le prix)"
            elif motif == "stop_initial" and not f["tp1_atteint"] and f["MFE_R"] is None:
                f["cause"] = "P(?): bougies absentes (MFE inconnu), non classé"
            else:
                f["cause"] = "P(f): bruit normal"

    # ---- matrice de performance (E1 retenus) ----
    pop = [f for f in fiches if f["retenu"] and f["era"] == "E1" and f["hyp"] in ("H1", "H2", "H3", "H4", "H5")]
    pop.sort(key=lambda f: f["ouvert_at"])
    cut = pop[int(0.6 * len(pop))]["ouvert_at"] if pop else ""
    rng = random.Random(SEED)
    cells, pv, matrix = {}, {}, {}
    for f in pop:
        cells.setdefault((f["hyp"], f["actif"]), []).append(f)
    for (h, a), L in sorted(cells.items()):
        tr = [x["R"] for x in L if x["ouvert_at"] < cut]
        te = [x["R"] for x in L if x["ouvert_at"] >= cut]
        allr = [x["R"] for x in L]
        lo, hi = boot_ci(allr, rng)
        matrix[f"{h}|{a}"] = {"n": len(allr), "R_moy": statistics.fmean(allr), "IC95": [lo, hi], "n_train": len(tr),
                              "R_train": statistics.fmean(tr) if tr else None, "n_test": len(te),
                              "R_test": statistics.fmean(te) if te else None, "statut_structurel": struct[h][a]["status"]}
        if len(te) >= 10:
            pv[f"{h}|{a}"] = p_neg(te, rng)
    adj = holm(pv) if pv else {}
    for k, m in matrix.items():
        m["p_holm_negatif"] = adj.get(k)
        if m["statut_structurel"] != "adapté":
            m["decision"] = "SUPPRIMER (structurel: %s)" % m["statut_structurel"]
        elif m["n_test"] >= 10 and m["p_holm_negatif"] is not None and m["p_holm_negatif"] < 0.05:
            m["decision"] = "SUPPRIMER (négatif significatif)"
        elif m["n_test"] >= 10 and m["R_test"] >= 0:
            m["decision"] = "CONCENTRER"
        else:
            m["decision"] = "CONSERVER - non concluant (n_test=%d)" % m["n_test"]

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "fiches_trades.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fiches[0].keys()))
        w.writeheader()
        w.writerows(fiches)
    summ = {"cut_train_test": cut, "n_E1_retenus": len(pop), "exclus": {k: len(v) for k, v in excluded.items()},
            "matrice": matrix, "manques": miss}
    (out / "synthese.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False), encoding="utf-8")
    print("fiches", len(fiches), "retenues", sum(f["retenu"] for f in fiches), "E1 retenues", len(pop), "cut", cut)
    print("exclus", summ["exclus"])


if __name__ == "__main__":
    main(*sys.argv[1:5])
