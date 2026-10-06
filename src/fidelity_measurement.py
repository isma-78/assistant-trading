"""
fidelity_measurement.py — Mesure de la fidélité du simulateur pour
H1-H4 v2 (07/10/2026), implémentation littérale de
`docs/PROTOCOLE_FIDELITE_07-10.md` (commit 02cc47f, écrit avant tout
calcul). Calcul pur — l'orchestration (lecture DB/`data/historical/`)
vit dans `scripts/_fidelite_h1h4_07-10.py`.

Réutilise `src.evolution_v2_test.TradeRef`/`pair_candidate_against_
baseline` SANS LES MODIFIER (baseline=live, candidate=backtest, diff_i
= R_backtest − R_live ; le biais signé de ce module est l'inverse,
R_live − R_backtest, voir §1.4 du protocole)."""

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

from src.evolution_v2_test import TradeRef, pair_candidate_against_baseline

MIN_N_ESTABLISHED = 20
MAX_MEAN_ABS_GAP = 0.30
MAX_ABS_SIGNED_BIAS = 0.15
MIN_CAUSE_SHARE_TO_MODEL = 0.20


@dataclass(frozen=True)
class FidelityMetrics:
    hypothesis: str
    n_live_eligible: int
    n_live_out_of_scope: int
    n_live_unreconciled: int
    n_paired: int
    pairing_rate: Optional[float]
    mean_abs_gap: Optional[float]
    mean_signed_bias: Optional[float]
    n_unmatched_live: int
    n_unmatched_backtest: int


def compute_fidelity_metrics(
    hypothesis: str, live: Sequence[TradeRef], backtest: Sequence[TradeRef],
    n_out_of_scope: int = 0, n_unreconciled: int = 0, tolerance_hours: float = 2.0,
) -> FidelityMetrics:
    """§1.3/§1.4 du protocole. `live` = trades éligibles seulement
    (ouvert_at < borne de données, déjà filtré par l'appelant)."""
    diffs, unmatched_live, unmatched_backtest = pair_candidate_against_baseline(live, backtest, tolerance_hours)
    n = len(live)
    n_paired = n - len(unmatched_live)
    pairing_rate = n_paired / n if n else None
    # diffs[i] = R_backtest − R_live (convention de pair_candidate_against_
    # baseline) ; le biais de ce module est l'inverse, R_live − R_backtest
    # (§1.4). Les trades non appariés sont exclus par IDENTITÉ d'objet
    # (jamais par égalité de valeur, qui confondrait deux trades
    # authentiquement identiques) — `unmatched_live` contient les MÊMES
    # objets que `live` (jamais des copies), vérifié dans
    # pair_candidate_against_baseline.
    unmatched_ids = {id(t) for t in unmatched_live}
    paired_bias = [-d for lv, d in zip(live, diffs) if id(lv) not in unmatched_ids]
    mean_abs_gap = mean(abs(b) for b in paired_bias) if paired_bias else None
    mean_signed_bias = mean(paired_bias) if paired_bias else None
    return FidelityMetrics(
        hypothesis, n, n_out_of_scope, n_unreconciled, len(paired_bias), pairing_rate,
        mean_abs_gap, mean_signed_bias, len(unmatched_live), len(unmatched_backtest),
    )


def estimate_date_n20(n_paired: int, first_pair_date: Optional[str], last_pair_date: Optional[str], now: datetime) -> Optional[str]:
    """Date estimée (ordre de grandeur, projection linéaire) où n=20
    paires sera atteint, à la cadence observée entre `first_pair_date`
    et `last_pair_date`. `None` si non estimable (0-1 paire, ou cadence
    nulle)."""
    if n_paired >= MIN_N_ESTABLISHED or n_paired < 2 or not first_pair_date or not last_pair_date:
        return None
    start = datetime.fromisoformat(first_pair_date[:19])
    end = datetime.fromisoformat(last_pair_date[:19])
    span_days = (end - start).total_seconds() / 86400
    if span_days <= 0:
        return None
    # `n_paired >= 2` (garde ci-dessus) et `span_days > 0` : rate_per_day
    # est donc toujours > 0 ici — pas de garde redondante sur une branche
    # inatteignable (même convention qu'ailleurs dans ce projet).
    rate_per_day = (n_paired - 1) / span_days
    days_needed = (MIN_N_ESTABLISHED - n_paired) / rate_per_day
    from datetime import timedelta
    return (now + timedelta(days=days_needed)).date().isoformat()


def fidelity_status(metrics: FidelityMetrics, n_min: int = MIN_N_ESTABLISHED) -> str:
    """§2 du protocole. Ne juge jamais la couverture temporelle
    (configuration antérieure à E1 ou non) — c'est à l'appelant de
    qualifier le statut en conséquence dans le rapport."""
    if metrics.n_paired < n_min:
        return "non établie (n insuffisant)"
    if metrics.mean_abs_gap is None or metrics.mean_signed_bias is None:
        return "non établie (n insuffisant)"
    if metrics.mean_abs_gap <= MAX_MEAN_ABS_GAP and abs(metrics.mean_signed_bias) <= MAX_ABS_SIGNED_BIAS:
        return "établie"
    return "non établie"


def tpfixe_counterfactual_r(partials: Sequence[Tuple[float, float]]) -> Optional[float]:
    """§1.5 cause 6 : R qu'aurait rendu une clôture à 100% au PREMIER
    événement de clôture (TP1 ou stop direct) au lieu de la structure
    partielle §2.10 — recalculé depuis la trajectoire backtest déjà
    simulée (`BacktestTrade.partials`), jamais une estimation live.
    `None` si aucune jambe (trade jamais fermé, ne devrait pas arriver
    pour un trade backtest terminé — fail-safe)."""
    if not partials:
        return None
    return partials[0][1]


def gap_share_explained(contributions: Sequence[Optional[float]], gaps: Sequence[float]) -> Optional[float]:
    """§1.5 : part de l'écart absolu total expliquée par UNE cause —
    somme des |contributions| connues / somme des |gaps| sur les MÊMES
    paires (jamais un dénominateur différent). `None` si aucune
    contribution n'est mesurable (toutes `None`) ou si `gaps` est vide."""
    if not gaps or sum(abs(g) for g in gaps) == 0:
        return None
    known = [(c, g) for c, g in zip(contributions, gaps) if c is not None]
    if not known:
        return None
    total_gap = sum(abs(g) for g in gaps)
    return sum(abs(c) for c, _ in known) / total_gap


def should_model_cause(share: Optional[float], threshold: float = MIN_CAUSE_SHARE_TO_MODEL) -> bool:
    """§5 : une cause n'est modélisée que si elle explique >= 20% de
    l'écart mesuré."""
    return share is not None and share >= threshold
