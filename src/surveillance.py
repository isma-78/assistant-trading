"""Surveillance automatique des hypothèses H1-H5 (29/10/2026).

Règles pré-enregistrées dans docs/regles_surveillance_2026-10-29.md (commit
antérieur à ce code). Déterministe, sans LLM, sans accès broker :
- données : trades ouverts APRÈS le début de l'époque P1_29-10, réconciliés ;
- n < 15 : « non concluant » ; n >= 15 : test t de Student unilatéral
  (H0 : R moyen >= 0), correction de Holm sur les 5 hypothèses ;
- suspension <=> H0 rejetée au seuil de Holm <=> borne haute de l'IC de
  Student (niveau 1 - 2 * alpha_Holm) < 0 ;
- une suspension est ÉCRITE (table `hypothesis_suspensions`, append-only) et
  n'est jamais levée par le code : redéploiement / décision humaine.
Fail-safe : toute erreur de lecture de la table de suspension équivaut à
« suspendu » (les entrées s'arrêtent, jamais ne se poursuivent)."""
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from src.db import connection_scope

logger = logging.getLogger(__name__)

EPOCH = "P1_29-10"
MIN_TRADES_FOR_RULE = 15
FAMILY_ALPHA = 0.05
CI_LEVEL_ALPHA = 0.05  # IC bilatéral 95 % affiché dans l'état quotidien

HYPOTHESIS_SOURCES: Dict[str, str] = {
    "H1": "hypothesis_v2",
    "H2": "hypothesis2_v2",
    "H3": "hypothesis3_v2",
    "H4": "hypothesis4_v2",
    "H5": "hypothesis5_v2",
}
SOURCE_TO_HYPOTHESIS: Dict[str, str] = {v: k for k, v in HYPOTHESIS_SOURCES.items()}

STATUS_ACTIVE = "actif"
STATUS_INCONCLUSIVE = "non concluant"
STATUS_SUSPENDED = "suspendu"


# ---------------------------------------------------------------------------
# Loi de Student (sans scipy)
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Fraction continue de la bêta incomplète (Lentz modifié)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _regularized_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """P(T <= t) pour une loi de Student à `df` degrés de liberté."""
    if df <= 0:
        raise ValueError("df doit être > 0")
    x = df / (df + t * t)
    tail = 0.5 * _regularized_beta(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def student_t_ppf(p: float, df: float) -> float:
    """Quantile de Student (bissection sur la CDF), 0 < p < 1."""
    if not 0.0 < p < 1.0:
        raise ValueError("p doit être dans ]0, 1[")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -student_t_ppf(1.0 - p, df)
    low, high = 0.0, 1.0
    while student_t_cdf(high, df) < p:
        high *= 2.0
        if high > 1e12:  # pragma: no cover — df >= 1 : quantile fini
            break
    for _ in range(200):
        mid = (low + high) / 2.0
        if student_t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# ---------------------------------------------------------------------------
# Statistiques d'un échantillon de R
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SampleStats:
    n: int
    mean: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    p_lower: Optional[float]  # test unilatéral H0 : moyenne >= 0 (petit p => moyenne < 0)


def sample_stats(values: Sequence[float], ci_alpha: float = CI_LEVEL_ALPHA) -> SampleStats:
    """Moyenne, IC bilatéral de Student (niveau 1 - ci_alpha) et p unilatéral.
    n < 2 : IC et p non calculables (None). Variance nulle : IC dégénéré
    sur la moyenne, p = 0 si moyenne < 0 sinon 1."""
    n = len(values)
    if n == 0:
        return SampleStats(0, None, None, None, None)
    mean = sum(values) / n
    if n < 2:
        return SampleStats(n, mean, None, None, None)
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    sd = math.sqrt(variance)
    if sd == 0.0:
        return SampleStats(n, mean, mean, mean, 0.0 if mean < 0 else 1.0)
    se = sd / math.sqrt(n)
    half = student_t_ppf(1.0 - ci_alpha / 2.0, n - 1) * se
    return SampleStats(n, mean, mean - half, mean + half, student_t_cdf(mean / se, n - 1))


def upper_bound_at_alpha(values: Sequence[float], one_sided_alpha: float) -> Optional[float]:
    """Borne haute de l'IC de Student de niveau 1 - 2 * alpha (équivalent au
    test unilatéral à `alpha`). None si n < 2."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    if sd == 0.0:
        return mean
    return mean + student_t_ppf(1.0 - one_sided_alpha, n - 1) * sd / math.sqrt(n)


# ---------------------------------------------------------------------------
# Règle de suspension (Holm sur la famille H1-H5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HypothesisVerdict:
    hypothesis: str
    n: int
    mean: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    p_value: Optional[float]
    holm_alpha: Optional[float]
    upper_bound_holm: Optional[float]
    status: str


def evaluate_hypotheses(
    r_by_hypothesis: Dict[str, Sequence[float]], already_suspended: Sequence[str] = (),
    min_trades: int = MIN_TRADES_FOR_RULE, family_alpha: float = FAMILY_ALPHA,
) -> Dict[str, HypothesisVerdict]:
    """Verdict par hypothèse. Famille de Holm = les 5 hypothèses (une
    hypothèse sous le seuil de n compte avec p = 1, conservateur). Une
    hypothèse déjà suspendue le reste (jamais levée par ce code)."""
    names = list(HYPOTHESIS_SOURCES)
    stats = {h: sample_stats(r_by_hypothesis.get(h, [])) for h in names}
    eligible_p = {h: (stats[h].p_lower if stats[h].n >= min_trades and stats[h].p_lower is not None else 1.0) for h in names}
    ordered = sorted(names, key=lambda h: (eligible_p[h], h))
    family = len(names)
    rejected = set()
    holm_alpha: Dict[str, float] = {}
    stop = False
    for rank, h in enumerate(ordered):
        threshold = family_alpha / (family - rank)
        holm_alpha[h] = threshold
        if not stop and eligible_p[h] <= threshold and stats[h].n >= min_trades:
            rejected.add(h)
        else:
            stop = True
    verdicts: Dict[str, HypothesisVerdict] = {}
    for h in names:
        st = stats[h]
        if h in already_suspended or h in rejected:
            status = STATUS_SUSPENDED
        elif st.n < min_trades:
            status = STATUS_INCONCLUSIVE
        else:
            status = STATUS_ACTIVE
        ub = upper_bound_at_alpha(r_by_hypothesis.get(h, []), holm_alpha[h]) if st.n >= min_trades else None
        verdicts[h] = HypothesisVerdict(
            h, st.n, st.mean, st.ci_low, st.ci_high, eligible_p[h] if st.n >= min_trades else None,
            holm_alpha[h], ub, status,
        )
    return verdicts


# ---------------------------------------------------------------------------
# Accès base
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS hypothesis_suspensions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis TEXT NOT NULL,
    source TEXT NOT NULL,
    suspended_at TEXT NOT NULL,
    n_trades INTEGER NOT NULL,
    mean_r REAL,
    upper_bound REAL,
    p_value REAL,
    holm_alpha REAL,
    reason TEXT NOT NULL
);
"""


def ensure_schema(db_path: str) -> None:
    with connection_scope(db_path) as conn:
        conn.executescript(SCHEMA)


def current_epoch(db_path: str) -> Optional[Tuple[str, str]]:
    """Époque en cours = la plus récente (started_at) des époques « P<n>_... »
    (P1_29-10, P2_..., etc.). Les règles de surveillance ne comptent QUE les
    trades de cette époque (29/10/2026 : l'époque P2 remplace P1 pour toute
    décision de suspension). None si aucune."""
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT epoch, MIN(started_at) AS s FROM hypothesis_epochs WHERE epoch GLOB 'P[0-9]*_*' "
            "GROUP BY epoch ORDER BY s DESC LIMIT 1"
        ).fetchone()
    return (row["epoch"], row["s"]) if row is not None and row["epoch"] is not None else None


def epoch_start(db_path: str, epoch: Optional[str] = None) -> Optional[str]:
    """Début de l'époque donnée, ou de l'époque en cours si `epoch` est omis."""
    if epoch is None:
        current = current_epoch(db_path)
        return current[1] if current else None
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT MIN(started_at) AS s FROM hypothesis_epochs WHERE epoch = ?", (epoch,)).fetchone()
    return row["s"] if row is not None else None


def fetch_post_epoch_trades(db_path: str, started_at: str) -> List[dict]:
    """Trades post-époque RÉCONCILIÉS : fermés, R présent, sans anomalie,
    chaque clôture exécutée (fraction > 0) avec un prix de sortie broker."""
    marks = ",".join("?" for _ in HYPOTHESIS_SOURCES)
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            f"SELECT t.id, t.source, t.actif, t.r_multiple_total AS r FROM trades t "
            f"WHERE t.statut = 'ferme' AND t.r_multiple_total IS NOT NULL AND t.anomalie_technique IS NULL "
            f"AND t.ouvert_at >= ? AND t.source IN ({marks}) "
            f"AND NOT EXISTS (SELECT 1 FROM trade_partials p WHERE p.trade_id = t.id AND p.fraction > 0 "
            f"AND p.prix_sortie_reel IS NULL) "
            f"AND EXISTS (SELECT 1 FROM trade_partials p WHERE p.trade_id = t.id AND p.fraction > 0)",
            (started_at, *HYPOTHESIS_SOURCES.values()),
        ).fetchall()
    return [dict(r) for r in rows]


def suspended_hypotheses(db_path: str) -> List[str]:
    with connection_scope(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT hypothesis FROM hypothesis_suspensions").fetchall()
    return sorted(r["hypothesis"] for r in rows)


def is_source_suspended(db_path: str, source: str) -> bool:
    """True si la source est suspendue. FAIL-SAFE : toute erreur de lecture
    vaut « suspendu » (les entrées s'arrêtent). Une source sans hypothèse
    (Station X, backtests) n'est jamais concernée."""
    hypothesis = SOURCE_TO_HYPOTHESIS.get(str(source))
    if hypothesis is None:
        return False
    try:
        ensure_schema(db_path)  # CREATE TABLE IF NOT EXISTS : table absente != suspension
        with connection_scope(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM hypothesis_suspensions WHERE hypothesis = ? LIMIT 1", (hypothesis,)
            ).fetchone()
        return row is not None
    except Exception:
        logger.exception("Lecture des suspensions impossible : entrées de %s bloquées (fail-safe)", source)
        return True


def run_daily_check(db_path: str, now_iso: str) -> Tuple[Dict[str, HypothesisVerdict], List[str], List[dict]]:
    """Évalue les règles, écrit les NOUVELLES suspensions (une ligne de
    journal chacune) et retourne (verdicts, nouvelles suspensions, trades).
    Époque absente : aucun verdict, aucune suspension (rien à évaluer)."""
    ensure_schema(db_path)
    started_at = epoch_start(db_path)
    if started_at is None:
        logger.warning("Surveillance : aucune époque P<n> enregistrée — aucune évaluation")
        return evaluate_hypotheses({}), [], []
    trades = fetch_post_epoch_trades(db_path, started_at)
    r_by_hypothesis: Dict[str, List[float]] = {h: [] for h in HYPOTHESIS_SOURCES}
    for t in trades:
        r_by_hypothesis[SOURCE_TO_HYPOTHESIS[t["source"]]].append(t["r"])
    already = suspended_hypotheses(db_path)
    verdicts = evaluate_hypotheses(r_by_hypothesis, already)
    newly: List[str] = []
    for h, v in verdicts.items():
        if v.status == STATUS_SUSPENDED and h not in already:
            reason = (
                f"{h} suspendue : n={v.n} >= {MIN_TRADES_FOR_RULE}, R moyen={v.mean:.3f}, borne haute IC Student "
                f"(Holm alpha={v.holm_alpha:.4f}) = {v.upper_bound_holm:.3f} < 0, p={v.p_value:.4f}"
            )
            with connection_scope(db_path) as conn:
                conn.execute(
                    "INSERT INTO hypothesis_suspensions (hypothesis, source, suspended_at, n_trades, mean_r, "
                    "upper_bound, p_value, holm_alpha, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (h, HYPOTHESIS_SOURCES[h], now_iso, v.n, v.mean, v.upper_bound_holm, v.p_value, v.holm_alpha, reason),
                )
                conn.execute(
                    "INSERT INTO logs (timestamp, level, module, message) VALUES (?, 'WARNING', 'surveillance', ?)",
                    (now_iso, reason),
                )
            logger.warning(reason)
            newly.append(h)
    return verdicts, newly, trades


def format_state(verdicts: Dict[str, HypothesisVerdict], trades: Sequence[dict], date_label: str,
                 started_at: Optional[str], entry_assets: Dict[str, Sequence[str]],
                 epoch_label: str = EPOCH) -> str:
    """État quotidien lisible : par hypothèse puis par hypothèse × actif."""
    def fmt(x: Optional[float]) -> str:
        return "—" if x is None else f"{x:+.3f}"

    lines = [
        f"# État de surveillance — {date_label}",
        "",
        f"Époque {epoch_label} (début {started_at or 'absent'}). Trades réconciliés postérieurs uniquement. "
        f"Règle : n >= {MIN_TRADES_FOR_RULE} et borne haute de l'IC de Student < 0 après Holm (5 hypothèses).",
        "",
        "## Par hypothèse",
        "",
        "| Hyp. | n | R moyen | IC 95 % | borne haute (Holm) | p | Statut |",
        "|---|---|---|---|---|---|---|",
    ]
    for h, v in verdicts.items():
        ci = "—" if v.ci_low is None else f"[{v.ci_low:+.3f} ; {v.ci_high:+.3f}]"
        p = "—" if v.p_value is None else f"{v.p_value:.4f}"
        lines.append(f"| {h} | {v.n} | {fmt(v.mean)} | {ci} | {fmt(v.upper_bound_holm)} | {p} | {v.status} |")
    lines += ["", "## Par hypothèse × actif", "", "| Hyp. | Actif | n | R moyen | IC 95 % | Statut |", "|---|---|---|---|---|---|"]
    for h, source in HYPOTHESIS_SOURCES.items():
        assets = sorted(set(entry_assets.get(h, [])) | {t["actif"] for t in trades if t["source"] == source})
        for asset in assets:
            values = [t["r"] for t in trades if t["source"] == source and t["actif"] == asset]
            st = sample_stats(values)
            ci = "—" if st.ci_low is None else f"[{st.ci_low:+.3f} ; {st.ci_high:+.3f}]"
            if verdicts[h].status == STATUS_SUSPENDED:
                status = STATUS_SUSPENDED
            elif st.n < MIN_TRADES_FOR_RULE:
                status = STATUS_INCONCLUSIVE
            else:
                status = STATUS_ACTIVE
            lines.append(f"| {h} | {asset} | {st.n} | {fmt(st.mean)} | {ci} | {status} |")
    lines.append("")
    return "\n".join(lines)
