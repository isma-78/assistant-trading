"""Étape d'analyse du backtest hypothèse x actif (29/10/2026) : lit data/backtest_29-10/trades_*.json,
applique src/adequation_decision (protocole docs/protocole_backtest_2026-10-29.md §7) et écrit
data/backtest_29-10/resultats.json + tableaux markdown. Aucune règle n'est modifiée ici."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import adequation_decision as ad  # noqa: E402
from src.hypothesis_asset_map import HYPOTHESIS_ENTRY_ASSETS  # noqa: E402
from src.simulator_fidelity import PortfolioTrade, apply_cluster_cap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "backtest_29-10"
STRUCT = ROOT / "data" / "analysis_29-10" / "structural.json"
FOREX = {"EURUSD", "GBPUSD", "USDJPY", "CHFJPY"}


def _fmt(x, nd=3):
    return "—" if x is None else f"{x:+.{nd}f}"


def load_variant(directory):
    raw = {}
    for p in sorted(Path(directory).glob("trades_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        raw[(d["hypothesis"], d["asset"])] = d
    trades = {k: [ad.Trade(t["entry_time"], t["exit_time"], t["r"]) for t in v["trades"]] for k, v in raw.items()}
    results = [ad.evaluate_pair(h, a, trades[(h, a)]) for (h, a) in sorted(trades)]
    ad.classify(results)
    return raw, trades, results


def main() -> int:
    raw_a, trades_a, res_a = load_variant(OUT)
    raw_b, trades_b, res_b = load_variant(OUT / "B")
    by_a = {(r.hypothesis, r.asset): r for r in res_a}
    by_b = {(r.hypothesis, r.asset): r for r in res_b}
    structural = json.loads(STRUCT.read_text(encoding="utf-8"))["table"]

    # Verdict final (amendement n°1) : CONCENTRER/SUPPRIMER seulement si A et B concordent.
    final = {}
    VB = {key: r.verdict for key, r in by_b.items()}  # verdict B seul, avant consensus
    for key in by_b:
        va, vb = by_a[key].verdict, by_b[key].verdict
        final[key] = va if va == vb else ad.INCONCLUSIVE
        by_b[key].verdict = final[key]

    mapping, rho_all = {}, {}
    for h in ["H1", "H2", "H3", "H4", "H5"]:
        hr = [r for r in res_b if r.hypothesis == h]
        weeks = sorted({w for r in hr for w in ad.weekly_r(trades_b[(h, r.asset)])})
        rho = {}
        for i, a in enumerate(hr):
            for b in hr[i + 1:]:
                rho[(a.asset, b.asset)] = ad.pearson(ad.weekly_r(trades_b[(h, a.asset)]), ad.weekly_r(trades_b[(h, b.asset)]), weeks)
        rho_all[h] = {f"{a}|{b}": round(v, 3) for (a, b), v in rho.items()}
        mapping[h] = ad.build_mapping(HYPOTHESIS_ENTRY_ASSETS[h], hr, rho)

    gaps = []
    for r in res_b:
        st = structural.get(r.hypothesis, {}).get(r.asset, {}).get("status")
        if st is None or r.test.mean is None:
            continue
        if st == "adapté" and r.test.mean < 0:
            gaps.append([r.hypothesis, r.asset, st, r.test.n, r.test.mean, r.verdict, "structure adaptée mais R test négatif"])
        if st != "adapté" and r.test.mean > 0:
            gaps.append([r.hypothesis, r.asset, st, r.test.n, r.test.mean, r.verdict, "structure non adaptée mais R test positif"])

    pool = []
    for (h, a), v in raw_b.items():
        if a in FOREX:
            pool += [PortfolioTrade(h, a, t["entry_time"], t["exit_time"], 10.0, t["r"])
                     for t in v["trades"] if t["entry_time"][:10] >= ad.TEST_START]
    kept, blocked = apply_cluster_cap(pool, 500.0 * 4.0 / 100.0) if pool else ([], [])

    def pack(r, raw):
        return {
            "n_train": r.train.n, "r_train": r.train.mean, "n_test": r.test.n, "r_test": r.test.mean,
            "ci": [r.test.ci_low, r.test.ci_high], "p_pos_holm": r.p_pos_holm, "p_neg_holm": r.p_neg_holm,
            "wf": r.positive_windows, "top3": r.test.top3_share, "dd": r.test.max_drawdown_r,
            "verdict": r.verdict, "reasons": r.reasons,
            "orders_placed": raw["orders_placed"], "orders_unfilled": raw["orders_unfilled"],
            "orders_prechecked_out": raw.get("orders_prechecked_out", 0),
        }

    pairs = []
    for key in sorted(by_b):
        a_res, b_res = by_a[key], by_b[key]
        a_verdict = a_res.verdict
        pairs.append({
            "h": key[0], "asset": key[1], "final": final[key],
            "A": pack(a_res, raw_a[key]) | {"verdict": a_verdict},
            "B": pack(b_res, raw_b[key]) | {"verdict": VB[key]},
            "structural": structural.get(key[0], {}).get(key[1], {}).get("status"),
        })
    out = {"mapping": mapping, "rho": rho_all, "cluster_cap_forex_test": {"trades": len(pool), "bloques": len(blocked)},
           "ecarts_structure": gaps, "pairs": pairs}
    (OUT / "resultats.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    lines = ["| Hyp | Actif | struct. | A: n test / R test | B: n test / R test | B IC95 | B p+ Holm | B p- Holm | WF (A/B) | top3 B | verdict A | verdict B | FINAL |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(by_b):
        a_res, b_res = by_a[key], by_b[key]
        st = structural.get(key[0], {}).get(key[1], {}).get("status", "—")
        ci = "—" if b_res.test.ci_low is None else f"[{b_res.test.ci_low:+.2f};{b_res.test.ci_high:+.2f}]"
        top3 = "—" if b_res.test.top3_share is None else f"{b_res.test.top3_share:.0%}"
        lines.append(
            f"| {key[0]} | {key[1]} | {st} | {a_res.test.n} / {_fmt(a_res.test.mean)} | {b_res.test.n} / {_fmt(b_res.test.mean)} | {ci} | "
            f"{'—' if b_res.p_pos_holm is None else f'{b_res.p_pos_holm:.3f}'} | {'—' if b_res.p_neg_holm is None else f'{b_res.p_neg_holm:.3f}'} | "
            f"{a_res.positive_windows}/{b_res.positive_windows} | {top3} | {a_res.verdict} | {VB[key]} | {final[key]} |"
        )
    (OUT / "matrice.md").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    print(chr(10).join(lines))
    print(chr(10) + "MAPPING:", json.dumps(mapping))
    print("CLUSTER:", out["cluster_cap_forex_test"])
    print("ECARTS:", len(gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
