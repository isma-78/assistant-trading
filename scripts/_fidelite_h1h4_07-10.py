"""
_fidelite_h1h4_07-10.py — Étape 1/2 du mandat du 07/10/2026 : mesure de
la fidélité du simulateur pour H1-H4 v2, selon
`docs/PROTOCOLE_FIDELITE_07-10.md` (commit 02cc47f, écrit avant ce
script). Lecture seule de `data/historical/` (coupé au 2026-09-25T18:00:00
UTC, borne avant E1/E2) et d'un instantané LOCAL de la base de production
(`data/snapshots/prod_07-10.db`, obtenu par une lecture SQL en lecture
seule, PAS un appel broker). Aucun ordre, aucun appel réseau vers
Capital.com dans ce script.

Usage : python scripts/_fidelite_h1h4_07-10.py
Sortie : data/snapshots/fidelity_results.json + impression lisible.
"""

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis1_strategy_v2 as h1_v2  # noqa: E402
import src.hypothesis2_strategy_v2 as h2_v2  # noqa: E402
import src.hypothesis3_strategy_v2 as h3_v2  # noqa: E402
import src.hypothesis4_strategy_v2 as h4_v2  # noqa: E402
from src.asset_whitelist import ASSET_WHITELIST  # noqa: E402
from src.backtest_engine import bar_from_raw, replay_hypothesis  # noqa: E402
from src.evolution_v2_test import TradeRef  # noqa: E402
from src.fidelity_measurement import (  # noqa: E402
    compute_fidelity_metrics,
    estimate_date_n20,
    fidelity_status,
    gap_share_explained,
    should_model_cause,
    tpfixe_counterfactual_r,
)
from src.risk_engine import RiskCaps, RiskEngine  # noqa: E402
from src.simulator_fidelity import PortfolioTrade, StopRefusalModel, apply_cluster_cap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "snapshots"
DB_PATH = OUT / "prod_07-10.db"

DATA_CUTOFF = "2026-09-25T18:00:00"  # dernière bougie HOUR locale (§1.1 du protocole)
EXTRA_SECONDS = {"HOUR_4": 14400.0, "DAY": 86400.0}
ENVELOPE = 500.0
ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]

# H2 : overrides réellement actifs par période (identique à
# scripts/_compare_live_vs_backtest_window.py/_a8_fidelity_report.py,
# repris tel quel — pas une nouvelle valeur).
H2_PARAM_PERIODS = [
    ("2026-08-30T00:00:00", "2026-09-25T18:14:16", {"EMA_PERIOD": 20, "RSI_THRESHOLD": 55.0, "N_TF": 3, "SCORE_THRESHOLD": 1.0}),
    ("2026-09-25T18:14:16", "2100-01-01T00:00:00", {}),
]

HYP = {
    "H1": {"source": "hypothesis_v2", "module": h1_v2, "extras": [], "donchian": False, "overrides": None},
    "H2": {"source": "hypothesis2_v2", "module": h2_v2, "extras": ["HOUR_4", "DAY"], "donchian": False, "overrides": H2_PARAM_PERIODS},
    "H3": {"source": "hypothesis3_v2", "module": h3_v2, "extras": [], "donchian": False, "overrides": None},
    "H4": {"source": "hypothesis4_v2", "module": h4_v2, "extras": [], "donchian": False, "overrides": None},
}


@contextmanager
def override(module, attrs):
    saved = {k: getattr(module, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(module, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(module, k, v)


_cache = {}


def load_bars(asset, resolution, start, end):
    """Charge une fois, borné à [2019-01-01, DATA_CUTOFF] — jamais la
    fenêtre scellée 2023-01-01->2024-06-15, jamais avant 2019-01-01, et de
    toute façon jamais au-delà de DATA_CUTOFF (ce mandat n'a aucune
    donnée plus récente, aucun appel broker)."""
    key = (asset, resolution)
    if key not in _cache:
        raw = json.loads((HIST / f"{asset}_{resolution}.json").read_text(encoding="utf-8"))
        bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None
                and "2019-01-01" <= b.time_utc <= DATA_CUTOFF
                and not ("2023-01-01" <= b.time_utc < "2024-06-15")]
        assert not any("2023-01-01" <= b.time_utc < "2024-06-15" for b in bars), "fenêtre scellée chargée"
        _cache[key] = bars
    return [b for b in _cache[key] if start <= b.time_utc < end]


def replay_period(module, extras, donchian, asset, start, end, stop_refusal, overrides):
    engine = RiskEngine(caps=RiskCaps(2.0, 4.0, ENVELOPE), whitelist=ASSET_WHITELIST)
    own_from = (datetime.strptime(start[:10], "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    kwargs = {"is_donchian_trailing": donchian, "stop_update_filter": StopRefusalModel() if stop_refusal else None}
    if extras:
        extra_from = (datetime.strptime(start[:10], "%Y-%m-%d") - timedelta(days=200)).strftime("%Y-%m-%d")
        kwargs.update(extra_resolution_bars={r: load_bars(asset, r, extra_from, end) for r in extras},
                      own_bar_duration_seconds=3600.0, extra_resolution_seconds={r: EXTRA_SECONDS[r] for r in extras})
    own_bars = load_bars(asset, "HOUR", own_from, end)
    if overrides is None:
        result = replay_hypothesis(asset, own_bars, module.evaluate_entry, engine, ASSET_WHITELIST, ENVELOPE, 0.75, **kwargs)
        return [t for t in result.trades if start <= t.entry_time_utc < end]
    trades = []
    for seg_start, seg_end, attrs in overrides:
        s, e = max(seg_start, start), min(seg_end, end)
        if s >= e:
            continue
        with override(module, attrs):
            result = replay_hypothesis(asset, own_bars, module.evaluate_entry, engine, ASSET_WHITELIST, ENVELOPE, 0.75, **kwargs)
        trades += [t for t in result.trades if s <= t.entry_time_utc < e]
    return trades


def load_live(conn, source, cutoff):
    eligible = conn.execute(
        "SELECT id, actif, direction, ouvert_at, r_multiple_total FROM trades WHERE source = ? AND statut = 'ferme' "
        "AND r_multiple_total IS NOT NULL AND ouvert_at < ? ORDER BY ouvert_at", (source, cutoff),
    ).fetchall()
    n_out_of_scope = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE source = ? AND statut = 'ferme' AND r_multiple_total IS NOT NULL AND ouvert_at >= ?",
        (source, cutoff),
    ).fetchone()[0]
    n_unreconciled = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE source = ? AND statut = 'ferme_non_reconcilie'", (source,),
    ).fetchone()[0]
    return eligible, n_out_of_scope, n_unreconciled


def load_causal_decomposition(conn, trade_ids):
    if not trade_ids:
        return {}
    placeholders = ",".join("?" for _ in trade_ids)
    rows = conn.execute(
        f"SELECT trade_id, cout_entree, cout_sortie, invalide FROM trade_causal_decomposition WHERE trade_id IN ({placeholders})",
        trade_ids,
    ).fetchall()
    return {r["trade_id"]: r for r in rows}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now()
    results = {}
    all_backtest_for_cluster = []  # portefeuille des 4 hypothèses, pour le plafond de cluster

    for key, cfg in HYP.items():
        print(f"\n===== {key} =====", flush=True)
        live_rows, n_oos, n_unrec = load_live(conn, cfg["source"], DATA_CUTOFF)
        live = [TradeRef(r["actif"], r["direction"], r["ouvert_at"], r["r_multiple_total"]) for r in live_rows]
        trade_ids = [r["id"] for r in live_rows]
        decomp = load_causal_decomposition(conn, trade_ids)

        window_start = min((r["ouvert_at"] for r in live_rows), default=None)
        backtest_trades = []
        if window_start is not None:
            for asset in ASSETS:
                backtest_trades += replay_period(cfg["module"], cfg["extras"], cfg["donchian"], asset,
                                                 window_start, DATA_CUTOFF, True, cfg["overrides"])
        backtest = [TradeRef(t.asset, t.direction, t.entry_time_utc, t.r_multiple_total) for t in backtest_trades]
        all_backtest_for_cluster += [PortfolioTrade(cfg["source"], t.asset, t.entry_time_utc, t.exit_time_utc,
                                                     10.0, t.r_multiple_total) for t in backtest_trades]

        metrics = compute_fidelity_metrics(key, live, backtest, n_oos, n_unrec)
        status = fidelity_status(metrics)
        date_n20 = estimate_date_n20(metrics.n_paired, window_start, max((r["ouvert_at"] for r in live_rows), default=None), now) \
            if status.startswith("non établie (n") else None

        print(f"  live éligibles={metrics.n_live_eligible} hors-portée(E1/E2)={metrics.n_live_out_of_scope} "
              f"non-réconciliés={metrics.n_live_unreconciled}")
        print(f"  backtest={len(backtest)} paires={metrics.n_paired} taux_appariement={metrics.pairing_rate}")
        print(f"  écart_absolu_moyen={metrics.mean_abs_gap} biais_signé_moyen={metrics.mean_signed_bias}")
        print(f"  non_appariés: live={metrics.n_unmatched_live} backtest={metrics.n_unmatched_backtest}")
        print(f"  STATUT FIDÉLITÉ: {status}" + (f" (n=20 estimé le {date_n20})" if date_n20 else ""))

        # --- Décomposition par cause (uniquement sur les paires) ---
        causes = {}
        if metrics.n_paired > 0:
            from src.evolution_v2_test import pair_candidate_against_baseline
            _, unmatched_live, _ = pair_candidate_against_baseline(live, backtest)
            unmatched_ids = {id(t) for t in unmatched_live}
            paired = [(lv, tid) for lv, tid in zip(live, trade_ids) if id(lv) not in unmatched_ids]
            gaps = [lv.r_multiple - next(bt.r_multiple for bt in backtest
                    if bt.asset == lv.asset and bt.direction == lv.direction
                    and abs(datetime.strptime(bt.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")
                            - datetime.strptime(lv.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")) <= timedelta(hours=2))
                    for lv, _ in paired]
            cout_entree = [decomp[tid]["cout_entree"] if tid in decomp and not decomp[tid]["invalide"] else None for _, tid in paired]
            cout_sortie = [decomp[tid]["cout_sortie"] if tid in decomp and not decomp[tid]["invalide"] else None for _, tid in paired]
            causes["remplissage_delai_entree"] = gap_share_explained(cout_entree, gaps)
            causes["spread_cout_sortie"] = gap_share_explained(cout_sortie, gaps)

            # Refus de resserrement : delta backtest avec/sans StopRefusalModel, par trade pairé (même entrée, sans refus).
            backtest_no_refusal = []
            for asset in ASSETS:
                backtest_no_refusal += replay_period(cfg["module"], cfg["extras"], cfg["donchian"], asset,
                                                     window_start, DATA_CUTOFF, False, cfg["overrides"])
            refusal_contrib = []
            for lv, _ in paired:
                bt_r = next((b.r_multiple for b in backtest if b.asset == lv.asset and b.direction == lv.direction
                            and abs(datetime.strptime(b.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")
                                    - datetime.strptime(lv.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")) <= timedelta(hours=2)), None)
                bt_no_r = next((b.r_multiple_total for b in backtest_no_refusal if b.asset == lv.asset and b.direction == lv.direction
                                and abs(datetime.strptime(b.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")
                                        - datetime.strptime(lv.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")) <= timedelta(hours=2)), None)
                refusal_contrib.append((bt_r - bt_no_r) if (bt_r is not None and bt_no_r is not None) else None)
            causes["refus_resserrement_stop"] = gap_share_explained(refusal_contrib, gaps)

            # Sortie (timing/étiquette) : contrefactuel TP-fixe depuis la trajectoire backtest déjà simulée.
            sortie_contrib = []
            for lv, _ in paired:
                bt = next((b for b in backtest_trades if b.asset == lv.asset and b.direction == lv.direction
                          and abs(datetime.strptime(b.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")
                                  - datetime.strptime(lv.entry_time_utc[:19], "%Y-%m-%dT%H:%M:%S")) <= timedelta(hours=2)), None)
                if bt is None:
                    sortie_contrib.append(None)
                    continue
                counterfactual = tpfixe_counterfactual_r(bt.partials)
                sortie_contrib.append((counterfactual - bt.r_multiple_total) if counterfactual is not None else None)
            causes["sortie_timing_etiquette"] = gap_share_explained(sortie_contrib, gaps)

            print("  causes (part de l'écart absolu, mesurée indépendamment) :")
            for cause, share in causes.items():
                flag = " -> À MODÉLISER (>=20%)" if should_model_cause(share) else ""
                print(f"    {cause}: {share}{flag}")

        results[key] = {
            "metrics": metrics.__dict__, "status": status, "date_n20": date_n20,
            "causes": causes, "n_backtest": len(backtest), "window_start": window_start,
        }

    # Plafond de cluster (portefeuille poolé des 4 hypothèses) : part des trades backtest SANS pendant live qui auraient été bloqués.
    provisional_risk = ENVELOPE * 4.0 / 100.0
    kept, blocked = apply_cluster_cap(all_backtest_for_cluster, provisional_risk)
    print(f"\n===== Plafond de cluster (portefeuille poolé) =====")
    print(f"  {len(blocked)}/{len(all_backtest_for_cluster)} trades backtest auraient été bloqués par le plafond de cluster")
    results["_cluster_cap"] = {"blocked": len(blocked), "total": len(all_backtest_for_cluster)}

    conn.close()
    (OUT / "fidelity_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
