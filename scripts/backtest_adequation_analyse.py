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


def main() -> int:
    raw = {}
    for p in sorted(OUT.glob("trades_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        raw[(d["hypothesis"], d["asset"])] = d
    trades = {k: [ad.Trade(t["entry_time"], t["exit_time"], t["r"]) for t in v["trades"]] for k, v in raw.items()}
    results = [ad.evaluate_pair(h, a, trades[(h, a)]) for (h, a) in sorted(trades)]
    ad.classify(results)
    structural = json.loads(STRUCT.read_text(encoding="utf-8"))["table"]

    mapping, rho_all, summary = {}, {}, {}
    for h in ["H1", "H2", "H3", "H4", "H5"]:
        hr = [r for r in results if r.hypothesis == h]
        weeks = sorted({w for r in hr for w in ad.weekly_r(trades[(h, r.asset)])})
        rho = {}
        for i, a in enumerate(hr):
            for b in hr[i + 1:]:
                rho[(a.asset, b.asset)] = ad.pearson(ad.weekly_r(trades[(h, a.asset)]), ad.weekly_r(trades[(h, b.asset)]), weeks)
        rho_all[h] = {f"{a}|{b}": round(v, 3) for (a, b), v in rho.items()}
        mapping[h] = ad.build_mapping(HYPOTHESIS_ENTRY_ASSETS[h], hr, rho)
        summary[h] = {r.asset: r.verdict for r in hr}

    # Écarts avec la structure théorique (lecture seule du fichier d'origine)
    gaps = []
    for r in results:
        st = structural.get(r.hypothesis, {}).get(r.asset, {}).get("status")
        if st is None or r.test.mean is None:
            continue
        if st == "adapté" and r.test.mean < 0:
            gaps.append((r.hypothesis, r.asset, st, r.test.n, r.test.mean, r.verdict, "structure adaptée mais R test négatif"))
        if st != "adapté" and r.test.mean > 0:
            gaps.append((r.hypothesis, r.asset, st, r.test.n, r.test.mean, r.verdict, "structure non adaptée mais R test positif"))

    # Plafond de cluster 50 € sur les trades forex du test (descriptif)
    pool = []
    for (h, a), v in raw.items():
        if a in FOREX:
            pool += [PortfolioTrade(h, a, t["entry_time"], t["exit_time"], 10.0, t["r"])
                     for t in v["trades"] if t["entry_time"][:10] >= ad.TEST_START]
    kept, blocked = apply_cluster_cap(pool, 500.0 * 4.0 / 100.0) if pool else ([], [])

    out = {
        "mapping": mapping, "verdicts": summary, "rho": rho_all,
        "cluster_cap_forex_test": {"trades": len(pool), "bloques": len(blocked)},
        "ecarts_structure": [list(g) for g in gaps],
        "pairs": [
            {
                "h": r.hypothesis, "asset": r.asset, "verdict": r.verdict, "reasons": r.reasons,
                "orders_placed": raw[(r.hypothesis, r.asset)]["orders_placed"],
                "orders_unfilled": raw[(r.hypothesis, r.asset)]["orders_unfilled"],
                "positive_windows": r.positive_windows, "p_pos_holm": r.p_pos_holm, "p_neg_holm": r.p_neg_holm,
                "train": r.train.__dict__, "test": r.test.__dict__, "windows": {k: v.__dict__ for k, v in r.windows.items()},
            }
            for r in results
        ],
    }
    (OUT / "resultats.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    lines = ["| Hyp | Actif | struct. | n train | R train | n test | R test | IC95 test | p+ Holm | p- Holm | WF | top3 | DD (R) | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        st = structural.get(r.hypothesis, {}).get(r.asset, {}).get("status", "—")
        ci = "—" if r.test.ci_low is None else f"[{r.test.ci_low:+.2f};{r.test.ci_high:+.2f}]"
        top3 = "—" if r.test.top3_share is None else f"{r.test.top3_share:.0%}"
        lines.append(
            f"| {r.hypothesis} | {r.asset} | {st} | {r.train.n} | {_fmt(r.train.mean)} | {r.test.n} | {_fmt(r.test.mean)} | {ci} | "
            f"{'—' if r.p_pos_holm is None else f'{r.p_pos_holm:.3f}'} | {'—' if r.p_neg_holm is None else f'{r.p_neg_holm:.3f}'} | "
            f"{r.positive_windows}/4 | {top3} | {r.test.max_drawdown_r:.1f} | {r.verdict} |"
        )
    (OUT / "matrice.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("\nMAPPING:", json.dumps(mapping))
    print("CLUSTER:", out["cluster_cap_forex_test"])
    print("ECARTS:", len(gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
