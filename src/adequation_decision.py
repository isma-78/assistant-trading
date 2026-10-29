"""Décision hypothèse × actif à partir d'un backtest (29/10/2026).

Règles pré-enregistrées dans docs/protocole_backtest_2026-10-29.md (§3, §5, §7),
commitées AVANT tout calcul de performance. Déterministe, sans LLM, sans accès
broker : ne fait que classer des listes de trades déjà simulés et construire un
mapping d'actifs d'ENTRÉE. Réutilise la loi de Student de `src.surveillance`.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from src.surveillance import sample_stats, student_t_cdf

TRAIN_START = "2019-01-01"
TEST_START = "2024-03-20"
WINDOWS: List[Tuple[str, str, str]] = [
    ("W1", "2019-01-01", "2021-01-01"),
    ("W2", "2021-01-01", "2023-01-01"),
    ("W3", "2023-01-01", "2025-01-01"),
    ("W4", "2025-01-01", "2100-01-01"),
]
MIN_TRADES_TEST = 30
MIN_TRADES_WINDOW = 5
MIN_POSITIVE_WINDOWS = 3
MAX_TOP3_SHARE = 0.50
ALPHA = 0.05
MAX_RHO = 0.60
MAX_ASSETS = 4

CONCENTRATE = "CONCENTRER"
REMOVE = "SUPPRIMER"
INCONCLUSIVE = "non concluant"


@dataclass(frozen=True)
class Trade:
    entry_time: str  # ISO, comparable lexicographiquement
    exit_time: str
    r: float  # R net de coûts


@dataclass(frozen=True)
class PeriodStats:
    n: int
    mean: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    p_lower: Optional[float]   # H0 : moyenne >= 0 (petit => moyenne < 0)
    p_upper: Optional[float]   # H0 : moyenne <= 0 (petit => moyenne > 0)
    t_stat: Optional[float]
    max_drawdown_r: float
    top3_share: Optional[float]


def max_drawdown(rs: Sequence[float]) -> float:
    """Plus grande baisse (positive) de la courbe cumulée en R."""
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def top3_share(rs: Sequence[float]) -> Optional[float]:
    """Part du profit brut réalisée par les 3 meilleurs trades ; None sans profit brut."""
    gains = sorted((r for r in rs if r > 0), reverse=True)
    gross = sum(gains)
    return sum(gains[:3]) / gross if gross > 0 else None


def period_stats(rs: Sequence[float]) -> PeriodStats:
    base = sample_stats(rs)
    n = base.n
    if n < 2 or base.mean is None:
        return PeriodStats(n, base.mean, None, None, None, None, None, max_drawdown(rs), top3_share(rs))
    mean = base.mean
    sd = math.sqrt(sum((v - mean) ** 2 for v in rs) / (n - 1))
    if sd == 0.0:
        p_lower = 0.0 if mean < 0 else 1.0
        p_upper = 0.0 if mean > 0 else 1.0
        t_stat = None
    else:
        t_stat = mean / (sd / math.sqrt(n))
        p_lower = student_t_cdf(t_stat, n - 1)
        p_upper = 1.0 - p_lower
    return PeriodStats(n, mean, base.ci_low, base.ci_high, p_lower, p_upper, t_stat, max_drawdown(rs), top3_share(rs))


def split_trades(trades: Sequence[Trade]) -> Dict[str, List[float]]:
    """R par période, affectation par date d'ENTRÉE : train, test, W1..W4."""
    out: Dict[str, List[float]] = {"train": [], "test": [], **{w[0]: [] for w in WINDOWS}}
    for t in sorted(trades, key=lambda x: x.entry_time):
        day = t.entry_time[:10]
        if day < TRAIN_START:
            continue
        out["test" if day >= TEST_START else "train"].append(t.r)
        for name, start, end in WINDOWS:
            if start <= day < end:
                out[name].append(t.r)
    return out


def holm_adjust(pvalues: Dict[str, float]) -> Dict[str, float]:
    """p ajustées de Holm (monotones, plafonnées à 1) pour une famille de taille len(pvalues)."""
    m = len(pvalues)
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    adjusted: Dict[str, float] = {}
    running = 0.0
    for rank, (key, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[key] = running
    return adjusted


@dataclass
class PairResult:
    hypothesis: str
    asset: str
    train: PeriodStats
    test: PeriodStats
    windows: Dict[str, PeriodStats]
    positive_windows: int = 0
    p_pos_holm: Optional[float] = None
    p_neg_holm: Optional[float] = None
    verdict: str = INCONCLUSIVE
    reasons: List[str] = field(default_factory=list)


def evaluate_pair(hypothesis: str, asset: str, trades: Sequence[Trade]) -> PairResult:
    parts = split_trades(trades)
    windows = {w[0]: period_stats(parts[w[0]]) for w in WINDOWS}
    positive = sum(1 for s in windows.values() if s.n >= MIN_TRADES_WINDOW and s.mean is not None and s.mean > 0)
    return PairResult(hypothesis, asset, period_stats(parts["train"]), period_stats(parts["test"]), windows, positive)


def classify(results: List[PairResult], alpha: float = ALPHA) -> List[PairResult]:
    """Applique §7 : deux familles de Holm (positif / négatif) sur les couples
    avec n_test >= 30, puis le verdict de chaque couple."""
    eligible = [r for r in results if r.test.n >= MIN_TRADES_TEST and r.test.p_upper is not None]
    pos = holm_adjust({f"{r.hypothesis}|{r.asset}": r.test.p_upper for r in eligible})
    neg = holm_adjust({f"{r.hypothesis}|{r.asset}": r.test.p_lower for r in eligible})
    for r in results:
        key = f"{r.hypothesis}|{r.asset}"
        if r.test.n < MIN_TRADES_TEST or r.test.p_upper is None:
            r.verdict, r.reasons = INCONCLUSIVE, [f"n_test={r.test.n} < {MIN_TRADES_TEST}"]
            continue
        r.p_pos_holm, r.p_neg_holm = pos[key], neg[key]
        if r.test.mean > 0 and r.p_pos_holm <= alpha:
            reasons = []
            if r.positive_windows < MIN_POSITIVE_WINDOWS:
                reasons.append(f"walk-forward {r.positive_windows}/4 < {MIN_POSITIVE_WINDOWS}")
            share = r.test.top3_share
            if share is not None and share > MAX_TOP3_SHARE:
                reasons.append(f"top-3 = {share:.0%} > {MAX_TOP3_SHARE:.0%} du profit brut")
            r.verdict = INCONCLUSIVE if reasons else CONCENTRATE
            r.reasons = reasons or ["positif net, significatif (Holm), robuste"]
        elif r.test.mean < 0 and r.p_neg_holm <= alpha:
            r.verdict, r.reasons = REMOVE, ["négatif net, significatif (Holm)"]
        else:
            r.verdict, r.reasons = INCONCLUSIVE, ["non significatif après Holm"]
    return results


# ---------------------------------------------------------------------------
# Corrélations et mapping
# ---------------------------------------------------------------------------

def weekly_r(trades: Sequence[Trade], start: str = TEST_START) -> Dict[str, float]:
    """R sommés par semaine ISO d'entrée (période >= start)."""
    out: Dict[str, float] = defaultdict(float)
    for t in trades:
        if t.entry_time[:10] < start:
            continue
        d = datetime.strptime(t.entry_time[:10], "%Y-%m-%d")
        monday = d - timedelta(days=d.weekday())
        out[monday.strftime("%Y-%m-%d")] += t.r
    return dict(out)


def pearson(a: Dict[str, float], b: Dict[str, float], weeks: Sequence[str]) -> float:
    """Corrélation de Pearson sur `weeks` (semaines sans trade = 0) ; 0.0 si indéfinie."""
    xs = [a.get(w, 0.0) for w in weeks]
    ys = [b.get(w, 0.0) for w in weeks]
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def build_mapping(
    current: Sequence[str], results: Sequence[PairResult], rho: Dict[Tuple[str, str], float],
    max_assets: int = MAX_ASSETS, max_rho: float = MAX_RHO,
) -> List[str]:
    """Mapping d'ENTRÉE d'une hypothèse = (actuel - SUPPRIMER) + CONCENTRER, sans
    paire corrélée > max_rho, au plus max_assets. Priorité : CONCENTRER par t décroissant,
    puis actifs actuels conservés dans leur ordre. Un actif jamais ajouté sans verdict CONCENTRER."""
    by_asset = {r.asset: r for r in results}
    removed = {a for a, r in by_asset.items() if r.verdict == REMOVE}
    concentrate = sorted(
        (r for r in results if r.verdict == CONCENTRATE), key=lambda r: -(r.test.t_stat or 0.0),
    )
    priority = [r.asset for r in concentrate] + [a for a in current if a not in removed]
    chosen: List[str] = []
    for asset in priority:
        if asset in chosen:
            continue
        if any(rho.get((asset, c), rho.get((c, asset), 0.0)) > max_rho for c in chosen):
            continue
        if len(chosen) >= max_assets:
            break
        chosen.append(asset)
    return chosen
