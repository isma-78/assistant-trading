"""
_adequation_structurelle_29-10.py — score d'adéquation structurelle
hypothèse × actif (docs/adequation_paires_2026-10-29.md §2-3). PRIX SEULS,
aucun résultat de trading. Lecture seule des bougies horaires.

Usage : python -I scripts/_adequation_structurelle_29-10.py <dossier_bougies> <sortie.json>
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest_engine import bar_from_raw  # noqa: E402
from src.structural_characteristics import (  # noqa: E402
    cost_over_atr, log_returns, variance_ratio, volatility_clustering,
)

ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
HYPS = ["H1", "H2", "H3", "H4", "H5"]
OLD = ("2019-01-01", "2023-01-01")
RECENT = ("2024-06-15", "2099-01-01")
CHFJPY_EXCLUDED = {"H2", "H3", "H4", "H5"}
CORR_MAX, MAX_ASSETS = 0.60, 4


def load(hist, asset, win):
    raw = json.loads((Path(hist) / f"{asset}_HOUR.json").read_text(encoding="utf-8"))
    return [b for b in (bar_from_raw(p) for p in raw) if b is not None and win[0] <= b.time_utc < win[1]]


def measures(bars):
    r = log_returns(bars)
    return {"n": len(bars), "C": cost_over_atr(bars), "P": variance_ratio(r), "V": volatility_clustering(r)}


def score_and_veto(h, m):
    s, veto = 0, []
    c = m["C"]
    cveto = 0.10 if h in ("H3", "H4") else 0.15
    s += 2 if c <= 0.05 else 1 if c <= 0.08 else 0
    if c > cveto:
        veto.append(f"C={c:.3f}>{cveto}")
    p = m["P"]
    if h == "H4":
        s += 2 if p <= 0.93 else 1 if p <= 0.97 else 0
        if p > 1.07:
            veto.append(f"P={p:.3f}>1.07")
    else:
        s += 2 if p >= 1.00 else 1 if p >= 0.95 else 0
        if p < 0.93:
            veto.append(f"P={p:.3f}<0.93")
    if h == "H5":
        v = m["V"]
        s += 2 if v >= 0.30 else 1 if v >= 0.25 else 0
        if v < 0.15:
            veto.append(f"V={v:.3f}<0.15")
    return s, veto


def pearson(x, y):
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = sum((a - mx) ** 2 for a in x) ** .5
    sy = sum((b - my) ** 2 for b in y) ** .5
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def hourly_returns(bars):
    out, prev = {}, None
    for b in bars:
        mid = (b.close_bid + b.close_ask) / 2 if hasattr(b, "close_bid") else b.close
        if prev:
            out[b.time_utc] = __import__("math").log(mid / prev)
        prev = mid
    return out


def corr_matrix(hist, win):
    rets = {a: hourly_returns(load(hist, a, win)) for a in ASSETS}
    res = {}
    for i, a in enumerate(ASSETS):
        for b in ASSETS[i + 1:]:
            keys = sorted(set(rets[a]) & set(rets[b]))
            res[f"{a}|{b}"] = pearson([rets[a][k] for k in keys], [rets[b][k] for k in keys]) if len(keys) > 200 else None
    return res


def main(hist, out):
    mo = {a: measures(load(hist, a, OLD)) for a in ASSETS}
    mr = {a: measures(load(hist, a, RECENT)) for a in ASSETS}
    co, cr = corr_matrix(hist, OLD), corr_matrix(hist, RECENT)
    table = {}
    for h in HYPS:
        table[h] = {}
        for a in ASSETS:
            so, vo = score_and_veto(h, mo[a])
            sr, vr = score_and_veto(h, mr[a])
            mx = 6 if h == "H5" else 4
            if a == "CHFJPY" and h in CHFJPY_EXCLUDED:
                status = "exclu (pré-enregistrement 29/08)"
            elif vo or vr:
                status = "inadapté"
            elif so < mx / 2:
                status = "douteux"
            else:
                status = "adapté"
            table[h][a] = {"score_old": so, "score_recent": sr, "max": mx, "veto_old": vo, "veto_recent": vr, "status": status}
    json.dump({"old": mo, "recent": mr, "corr_old": co, "corr_recent": cr, "table": table}, open(out, "w"), indent=1)
    print("asset      C_old  P_old  V_old | C_rec  P_rec  V_rec")
    for a in ASSETS:
        o, r = mo[a], mr[a]
        print(f"{a:8s} {o['C']:.3f} {o['P']:.3f} {o['V']:.3f} | {r['C']:.3f} {r['P']:.3f} {r['V']:.3f}  (n {o['n']}/{r['n']})")
    for h in HYPS:
        print(h, {a: f"{t['status']} {t['score_old']}/{t['max']}" for a, t in table[h].items()})
    hi = sorted(((k, v, cr.get(k)) for k, v in co.items() if v is not None and abs(v) > CORR_MAX), key=lambda t: -abs(t[1]))
    print("corr > 0.6:", [(k, round(v, 2), None if w is None else round(w, 2)) for k, v, w in hi])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
