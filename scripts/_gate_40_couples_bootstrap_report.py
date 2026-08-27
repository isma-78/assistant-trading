"""
_gate_40_couples_bootstrap_report.py — etape 1 (chaine complete,
27/08/2026) : rapporte, pour les couples (hypothese, actif) disposant de
donnees `*_backtest` REGENEREES avec le modele de couts corrige
(costfix_staging/data/assistant_trading_staging.db, voir
docs/DECISIONS.md 26/08/2026), combien passent le critere DURCI propose
pour le reel : borne basse a 95% (bootstrap par blocs calendaires,
src.evolution_engine.compute_calendar_block_bootstrap_lower_bound) > 0.

Lecture seule sur la base ISOLEE (jamais la production). Aucune ecriture.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evolution_engine import compute_calendar_block_bootstrap_lower_bound, compute_lower_bound
import math

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "assistant_trading_staging.db"


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT source, actif, r_multiple_total, ferme_at FROM trades "
        "WHERE source LIKE '%_backtest' AND statut='ferme' AND r_multiple_total IS NOT NULL "
        "ORDER BY source, actif, ferme_at"
    ).fetchall()

    by_couple = {}
    for r in rows:
        key = (r["source"], r["actif"])
        by_couple.setdefault(key, {"r": [], "ts": []})
        by_couple[key]["r"].append(r["r_multiple_total"])
        by_couple[key]["ts"].append(r["ferme_at"])

    print(f"{len(by_couple)} couples (hypothese, actif) avec au moins 1 trade backtest corrige.\n")
    print("| Hypothese | Actif | n | mois distincts | mean | borne basse (z*SE, analytique) | borne basse (bootstrap blocs mois) | PASSE (bootstrap>0) |")
    print("|---|---|---|---|---|---|---|---|")

    z95 = 1.6449  # unilateral 95%, non corrige Bonferroni ici (rapport, pas selection multiple)
    n_pass = 0
    n_evaluable = 0
    for (source, actif), data in sorted(by_couple.items()):
        r_values, timestamps = data["r"], data["ts"]
        n = len(r_values)
        mean = sum(r_values) / n
        n_months = len(set(ts[:7] for ts in timestamps))
        lb_analytic = compute_lower_bound(r_values, z95)
        lb_bootstrap = compute_calendar_block_bootstrap_lower_bound(r_values, timestamps, seed=0)
        passed = lb_bootstrap is not None and lb_bootstrap > 0
        if lb_bootstrap is not None:
            n_evaluable += 1
        if passed:
            n_pass += 1
        lb_a_str = f"{lb_analytic:.4f}" if lb_analytic is not None else "N/A"
        lb_b_str = f"{lb_bootstrap:.4f}" if lb_bootstrap is not None else "N/A (1 seul mois)"
        print(f"| {source} | {actif} | {n} | {n_months} | {mean:.4f} | {lb_a_str} | {lb_b_str} | {'OUI' if passed else 'non'} |")

    print(f"\n=== {n_pass} / {n_evaluable} couples evaluables passent 'borne basse bootstrap > 0' ===")
    print(f"(sur {len(by_couple)} couples au total ayant >=1 trade ; {len(by_couple) - n_evaluable} non evaluables : 1 seul mois calendaire)")
    total_possible = 40
    zero_trade = total_possible - len(by_couple)
    print(f"Couples a 0 trade (aucune donnee backtest) : {zero_trade} / {total_possible}")


if __name__ == "__main__":
    main()
