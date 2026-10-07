"""
_execution_metrics_08-10.py — Étape 4 du mandat autonome du 08/10/2026
(Partie 1), selon `docs/PROTOCOLE_AUTONOME_08-10.md` §3. Lecture seule :
aucun ordre, aucun appel réseau. Même instantané local que l'étape 3
(`data/snapshots/prod_07-10.db`, pas d'accès VPS dans cette Partie 1).

Population : TOUS les trades/signaux (pas seulement les réconciliés) de
la configuration actuelle, `ouvert_at` (ou équivalent pour les lignes
non ouvertes : `signals.id` lié) dans
[2026-09-25T19:35:00, 2026-10-07T04:00:00[, CHFJPY comprise ici (l'étape
4 ne porte pas la même règle d'exclusion que la fidélité — le protocole
§3 ne l'exclut pas, seul §1 l'exige).

Limites réelles constatées EN LISANT LE CODE AVANT tout calcul (consigné
dans docs/AUTONOMIE_08-10.md) :
- le refus de resserrement de stop n'est journalisé QUE dans les fichiers
  de log du VPS (`logger.exception`, `src/executor.py::_push_stop_update`),
  jamais en base — **non remesurable sans accès VPS** dans cette Partie 1.
  Les seuls chiffres disponibles restent ceux de `docs/BILAN_05-10.md`
  §2.3 (mesurés depuis les fichiers de log, une session avec accès VPS),
  rapportés tels quels, JAMAIS présentés comme remesurés ici.
- A3 (motif dédié `limite_refusee`) est déjà déployé
  (`executor._classify_placement_failure`) — mais 0 occurrence sur cette
  population (voir §2 ci-dessous) : tous les échecs de placement de cette
  fenêtre sont `autre_echec_placement`, jamais `limite_refusee`.
- le délai de remplissage (statut `en_attente` -> `ouvert`) n'a pas de
  colonne dédiée ; approché par `trades.ouvert_at` moins le
  `market_snapshots.captured_at` du signal d'origine, quand ce lien
  existe (couverture rapportée, jamais une estimation pour les trades
  sans lien).
"""

import json
import math
import sqlite3
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spread_analysis import hourly_spread_by_asset, utc_hour_from_timestamp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "snapshots"
DB_PATH = OUT / "prod_07-10.db"
SPECS_PATH = ROOT / "data" / "instrument_specs.json"

POPULATION_START = "2026-09-25T19:35:00"
DATA_CUTOFF = "2026-10-07T04:00:00"
SOURCES = ["hypothesis_v2", "hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2"]
HYP_LABELS = {"hypothesis_v2": "H1", "hypothesis2_v2": "H2", "hypothesis3_v2": "H3", "hypothesis4_v2": "H4"}

MIN_N_FOR_CELL_VERDICT = 30  # §3 du protocole : aucun verdict de cellule (hypothèse, actif) sous ce seuil

_PLACEMENT_FAILURE_MOTIFS = ("rate_limit_429", "stop_refuse", "limite_refusee", "autre_echec_placement")

# MDE0 réutilisé tel quel de docs/BILAN_05-10.md §4 (jamais recalculé ici) :
# jalon n=30, sigma~=1R pour H1-H4 (table "σ ≈ 1 R (H1-H4)").
MDE0_N30_SIGMA1 = 0.69


def _tick_sizes():
    raw = json.loads(SPECS_PATH.read_text(encoding="utf-8"))
    sizes = {}
    for entry in raw.get("assets", {}).values():
        primary = entry.get("primary") or {}
        epic, tick = primary.get("epic"), primary.get("minStepDistance_value")
        if epic and tick:
            sizes[epic] = float(tick)
    if "CHFJPY" not in sizes and "USDJPY" in sizes:
        sizes["CHFJPY"] = sizes["USDJPY"]
    return sizes


def fetch_trades(conn):
    return conn.execute(
        "SELECT * FROM trades WHERE source IN (?,?,?,?) AND ouvert_at >= ? AND ouvert_at < ?",
        (*SOURCES, POPULATION_START, DATA_CUTOFF),
    ).fetchall()


def fill_rate_matrix(trades):
    """§3 : taux de remplissage = c/(b+c), (a) = échec de placement
    exclu du dénominateur (même règle que `src.fill_rate_analysis`,
    rejouée ici avec la fenêtre de ce mandat — le module existant
    n'est pas fenêtré, jamais modifié)."""
    cells = {}
    for t in trades:
        key = (HYP_LABELS[t["source"]], t["actif"])
        c = cells.setdefault(key, {"a": 0, "b": 0, "c": 0})
        if t["statut"] in ("ouvert", "ferme", "ferme_non_reconcilie"):
            c["c"] += 1
        elif t["statut"] == "annule" and t["annulation_motif"] == "peremption_marche":
            c["b"] += 1
        elif t["statut"] == "annule" and t["annulation_motif"] in _PLACEMENT_FAILURE_MOTIFS:
            c["a"] += 1
    rows = []
    for (hyp, actif), c in sorted(cells.items()):
        b_plus_c = c["b"] + c["c"]
        n_total = c["a"] + b_plus_c
        taux = c["c"] / b_plus_c if b_plus_c else None
        rows.append({"hypothese": hyp, "actif": actif, **c, "n_total": n_total,
                     "taux_remplissage": taux, "verdict_possible": n_total >= MIN_N_FOR_CELL_VERDICT})
    return rows


def pooled_fill_rate(trades):
    a = sum(1 for t in trades if t["statut"] == "annule" and t["annulation_motif"] in _PLACEMENT_FAILURE_MOTIFS)
    b = sum(1 for t in trades if t["statut"] == "annule" and t["annulation_motif"] == "peremption_marche")
    c = sum(1 for t in trades if t["statut"] in ("ouvert", "ferme", "ferme_non_reconcilie"))
    b_plus_c = b + c
    return {"echec_placement": a, "peremption_marche": b, "rempli": c, "n_b_plus_c": b_plus_c,
            "taux_remplissage": (c / b_plus_c if b_plus_c else None), "n_total": a + b_plus_c,
            "verdict_possible": (a + b_plus_c) >= MIN_N_FOR_CELL_VERDICT}


def entry_slippage_ticks(trades, tick_sizes):
    """Glissement d'entrée en ticks = (prix_entree_reel - prix_entree_prevu) / tick."""
    by_hyp_asset = {}
    for t in trades:
        if t["prix_entree_reel"] is None or t["prix_entree_prevu"] is None:
            continue
        tick = tick_sizes.get(t["actif"])
        if not tick:
            continue
        key = (HYP_LABELS[t["source"]], t["actif"])
        by_hyp_asset.setdefault(key, []).append((t["prix_entree_reel"] - t["prix_entree_prevu"]) / tick)
    return {k: {"n": len(v), "glissement_moyen_ticks": sum(v) / len(v)} for k, v in by_hyp_asset.items()}


def spread_at_entry_vs_median(conn, trades):
    """Spread au signal (`market_snapshots.spread`) vs la moyenne horaire
    déjà mesurée par `src.spread_analysis.hourly_spread_by_asset`
    (réutilisée inchangée — son docstring dit "moyen", le protocole dit
    "médian" ; aucune divergence de VALEUR introduite ici, consigné dans
    docs/AUTONOMIE_08-10.md)."""
    hourly = {(r["actif"], r["heure_utc"]): r["spread_moyen"] for r in hourly_spread_by_asset(str(DB_PATH))}
    rows = []
    for t in trades:
        row = conn.execute(
            "SELECT m.spread AS spread, m.captured_at AS captured_at FROM market_snapshots m "
            "WHERE m.signal_id = ? AND m.spread IS NOT NULL", (t["signal_id"],),
        ).fetchone()
        if row is None:
            continue
        hour = utc_hour_from_timestamp(row["captured_at"])
        reference = hourly.get((t["actif"], hour))
        if reference is None:
            continue
        rows.append({"hypothese": HYP_LABELS[t["source"]], "actif": t["actif"],
                     "spread_signal": row["spread"], "reference_horaire": reference,
                     "ecart": row["spread"] - reference})
    return rows


def fill_delay_coverage(conn, trades):
    """Délai de remplissage approché par ouvert_at - captured_at du
    `market_snapshots` lié au signal — couverture rapportée, jamais une
    estimation inventée pour les trades sans snapshot lié."""
    mesurable, total = 0, 0
    delays = []
    for t in trades:
        if t["statut"] not in ("ouvert", "ferme", "ferme_non_reconcilie"):
            continue
        total += 1
        row = conn.execute(
            "SELECT captured_at FROM market_snapshots WHERE signal_id = ?", (t["signal_id"],),
        ).fetchone()
        if row is None or row["captured_at"] is None or t["ouvert_at"] is None:
            continue
        try:
            from datetime import datetime
            delay_s = (datetime.fromisoformat(t["ouvert_at"][:19]) - datetime.fromisoformat(row["captured_at"][:19])).total_seconds()
        except ValueError:
            continue
        if delay_s < 0:
            continue
        mesurable += 1
        delays.append(delay_s)
    return {"n_mesurable": mesurable, "n_total_rempli": total,
            "delai_median_secondes": median(delays) if delays else None}


def cost_per_trade_r(conn, trades):
    ids_by_hyp = {}
    for t in trades:
        if t["statut"] == "ferme" and t["r_multiple_total"] is not None:
            ids_by_hyp.setdefault(HYP_LABELS[t["source"]], []).append(t["id"])
    out = {}
    for hyp, ids in ids_by_hyp.items():
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT cout_entree, cout_sortie FROM trade_causal_decomposition "
            f"WHERE trade_id IN ({placeholders}) AND invalide = 0", ids,
        ).fetchall()
        totals = [(r["cout_entree"] or 0.0) + (r["cout_sortie"] or 0.0) for r in rows
                  if r["cout_entree"] is not None or r["cout_sortie"] is not None]
        out[hyp] = {"n": len(totals), "cout_moyen_r": (sum(totals) / len(totals)) if totals else None}
    return out


def brut_min(cout0: float, mde0: float) -> float:
    """§3 : constat de faisabilité, jamais un verdict ni une sélection."""
    return 2 * math.sqrt(abs(cout0) * mde0)


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    trades = fetch_trades(conn)
    tick_sizes = _tick_sizes()

    fill_matrix = fill_rate_matrix(trades)
    pooled_fill = pooled_fill_rate(trades)
    slippage = entry_slippage_ticks(trades, tick_sizes)
    spread_rows = spread_at_entry_vs_median(conn, trades)
    fill_delay = fill_delay_coverage(conn, trades)
    cost_r = cost_per_trade_r(conn, trades)

    print(f"Population : {len(trades)} lignes trades, {POPULATION_START} -> {DATA_CUTOFF}")
    print("\n=== Matrice (hypothèse, actif) — AUCUN verdict de cellule sous n=30 ===")
    for row in fill_matrix:
        print(f"  {row}")
    print(f"\n=== Pooled (toutes cellules) === {pooled_fill}")

    print("\n=== Glissement d'entrée (ticks) ===")
    for k, v in sorted(slippage.items()):
        print(f"  {k}: {v}")

    print(f"\n=== Spread signal vs référence horaire : n={len(spread_rows)} paires mesurables ===")
    print("  (toujours PAR ACTIF — jamais poolé en valeur brute, les actifs n'ont pas la même échelle de prix)")
    spread_by_asset = {}
    for r in spread_rows:
        spread_by_asset.setdefault(r["actif"], []).append(r["ecart"])
    for actif, ecarts in sorted(spread_by_asset.items()):
        print(f"  {actif}: n={len(ecarts)} écart moyen (signal - référence horaire) = {sum(ecarts) / len(ecarts):.6f}")

    print(f"\n=== Délai de remplissage === {fill_delay}")

    print("\n=== Coût par trade en R, et brut_min (MDE0 réutilisé, n=30, sigma~=1R, BILAN_05-10 §4) ===")
    brut_min_rows = {}
    for hyp, c in cost_r.items():
        if c["cout_moyen_r"] is None:
            continue
        bm = brut_min(c["cout_moyen_r"], MDE0_N30_SIGMA1)
        brut_min_rows[hyp] = {"n": c["n"], "cout0_r": c["cout_moyen_r"], "mde0_reutilise": MDE0_N30_SIGMA1, "brut_min": bm}
        print(f"  {hyp}: n={c['n']} coût0={c['cout_moyen_r']:.4f}R brut_min={bm:.4f}R (constat de faisabilité, pas un verdict)")

    results = {
        "population_start": POPULATION_START, "data_cutoff": DATA_CUTOFF, "n_trades": len(trades),
        "matrice_hyp_actif": fill_matrix, "pooled_fill_rate": pooled_fill,
        "glissement_entree_ticks": {f"{k[0]}|{k[1]}": v for k, v in slippage.items()},
        "n_spread_pairs": len(spread_rows), "spread_par_actif": spread_rows, "delai_remplissage": fill_delay,
        "cout_par_trade_r_et_brut_min": brut_min_rows,
    }
    (OUT / "execution_metrics_08-10.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    conn.close()
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
