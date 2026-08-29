"""
run_evolution_cycle.py — Version "moteur" (écrit dans rule_changes) de
scripts/evaluate_hypothesis_candidates.py (qui reste tel quel, lecture
seule stdout). Généralise ce script existant pour :
1. Réutiliser evolution_engine.py (sélection par lot statistique,
   correction Bonferroni, écriture rule_changes) au lieu d'un choix
   manuel en lisant le rapport.
2. Permettre un sous-ensemble d'actifs par hypothèse (`assets`) au lieu
   des 8 actifs fixes — nécessaire pour l'Hypothèse #1, dont le
   diagnostic (26/08/2026, voir docs/HYPOTHESES.md) est asset par
   asset, PAS pour retester du paramétrage par-actif (explicitement
   écarté le 25/08/2026, voir docs/HYPOTHESES.md — "PAS de paramétrage
   par-actif dans ce chantier"), mais pour choisir QUELS actifs restent
   dans le pool avant un rejeu poolé sur ce sous-ensemble, exactement la
   même convention que pour H2-H5.

Ne fait tourner AUCUN cycle sans qu'on le lui demande explicitement
(argument --hypothesis obligatoire) : conforme à la décision du
26/08/2026 de garder le déclenchement manuel (voir docs/DECISIONS.md) —
ce script automatise le CALCUL, jamais la décision de LANCER un cycle
ni le choix des candidats testés (pré-enregistrés ci-dessous, avec leur
justification théorique, AVANT tout calcul).

Aucun appel réseau, aucune donnée nouvelle téléchargée — lit
data/historical/ déjà présent (même contrat que
evaluate_hypothesis_candidates.py). Écrit dans rule_changes de la base
pointée par --db (defaut : data/assistant_trading.db).

Usage :
    python scripts/run_evolution_cycle.py --hypothesis H1
    python scripts/run_evolution_cycle.py --hypothesis H1 --dry-run
"""

import argparse
import json
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.hypothesis2_strategy as h2_mod
import src.hypothesis3_strategy as h3_mod
import src.hypothesis5_strategy as h5_mod
import src.mean_reversion_strategy as h4_mod
import src.trend_strategy as h1_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.audit_notifier import send_notification
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.config import load_config
from src.confidence_scorer import PHASE_A_MIN_TRADES_BACKTEST, PHASE_B_MIN_TRADES_BACKTEST
from src.evolution_engine import CandidateSpec, TrainingResult, run_evolution_cycle
from src.risk_engine import RiskCaps, RiskEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
DEFAULT_DB_PATH = str(PROJECT_ROOT / "data" / "assistant_trading.db")
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

# Coupure identique à tous les chantiers précédents (25-26/08/2026) —
# jamais changée d'un cycle à l'autre sans le documenter explicitement.
CUTOFF = "2025-12-01T00:00:00"

ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str, resolution: str) -> List[HistoricalBar]:
    key = f"{epic}_{resolution}"
    if key in _bar_cache:
        return _bar_cache[key]
    path = HISTORICAL_DIR / f"{key}.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[key] = bars
    return bars


def _split(bars: List[HistoricalBar], cutoff: str) -> tuple:
    train = [b for b in bars if b.time_utc < cutoff]
    validation = [b for b in bars if b.time_utc >= cutoff]
    return train, validation


@contextmanager
def _override_attrs(module, **kwargs):
    old = {k: getattr(module, k) for k in kwargs}
    for k, v in kwargs.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(module, k, v)


@contextmanager
def _override_bollinger_std(std_multiplier: float):
    func = h4_mod.compute_bollinger_bands
    old_defaults = func.__defaults__
    period = old_defaults[0]
    func.__defaults__ = (period, std_multiplier)
    try:
        yield
    finally:
        func.__defaults__ = old_defaults


@contextmanager
def _apply_candidate(module, overrides: dict):
    overrides = dict(overrides)
    bollinger_std = overrides.pop("_bollinger_std", None)
    with _override_attrs(module, **overrides):
        if bollinger_std is not None:
            with _override_bollinger_std(bollinger_std):
                yield
        else:
            yield


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


# ---------------------------------------------------------------------------
# Registre des hypothèses — candidats PRÉ-ENREGISTRÉS avec justification
# théorique AVANT tout calcul (invariant #10). Ajouter un candidat ici
# SANS l'avoir d'abord écrit dans docs/HYPOTHESES.md est un contournement
# de l'invariant, pas un raccourci.
# ---------------------------------------------------------------------------

HYPOTHESES = {
    "H1": {
        "module": h1_mod, "resolution": "HOUR", "require_regime": False, "is_donchian": True,
        # Diagnostic Phase 1 (26/08/2026, voir docs/HYPOTHESES.md) : sur
        # les 4 couples bloqués en direct par Option B, USDJPY/GBPUSD/
        # EURUSD ont un edge BRUT positif (coût structurel, pas absence
        # d'edge) ; US30 n'a pas d'edge réel même brut -> retiré du pool
        # de ce chantier (Branche B, abandon, comme H4), PAS retesté ici.
        # Aucun paramétrage par-actif (cohérent avec la décision du
        # 25/08/2026) : les 3 actifs retenus sont poolés, comme H2-H5.
        "assets": ["USDJPY", "GBPUSD", "EURUSD"],
        "candidates": {
            "A": CandidateSpec(name="A"),
            # Résolution HOUR_4 : théorie = le coût mesuré en Phase 1
            # (spread/financement) pèse relativement moins sur un stop
            # basé sur une bougie plus large, au prix d'un débit de
            # trades plus faible — même théorie que la Branche A H3/H5
            # (25/08/2026), jamais testée pour H1 jusqu'ici.
            "B_HOUR_4": CandidateSpec(
                name="B_HOUR_4", overrides={"resolution_entree": "HOUR_4"},
                theory=(
                    "Coût relatif (spread+financement) mesuré plus faible sur bougie "
                    "4h que 1h pour un stop Donchian(20) équivalent (voir diagnostic "
                    "Phase 1, docs/HYPOTHESES.md 26/08/2026) — hypothèse testée, pas "
                    "supposée vraie."
                ),
            ),
        },
    },
}


def _resolution_for(hyp_key: str, candidate: CandidateSpec) -> str:
    cfg = HYPOTHESES[hyp_key]
    return candidate.overrides.get("resolution_entree", cfg["resolution"])


def _run_pooled(hyp_key: str, candidate: CandidateSpec, split_index: int) -> TrainingResult:
    """split_index: 0 = entraînement, 1 = validation."""
    cfg = HYPOTHESES[hyp_key]
    resolution = _resolution_for(hyp_key, candidate)
    entry_fn = cfg["module"].evaluate_entry
    risk_engine = _make_risk_engine()
    all_r: List[float] = []

    plain_overrides = {k: v for k, v in candidate.overrides.items() if k != "resolution_entree"}
    with _apply_candidate(cfg["module"], plain_overrides):
        for asset in cfg["assets"]:
            bars = _load_bars(asset, resolution)
            train_bars, validation_bars = _split(bars, CUTOFF)
            own_bars = (train_bars, validation_bars)[split_index]
            if len(own_bars) < 50:
                continue
            result = replay_hypothesis(
                asset, own_bars, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
                require_regime_confirmation=cfg["require_regime"], is_donchian_trailing=cfg["is_donchian"],
            )
            all_r.extend(t.r_multiple_total for t in result.trades)

    return TrainingResult(candidate.name, n_trades=len(all_r), r_values=tuple(all_r))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", required=True, choices=sorted(HYPOTHESES.keys()))
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="N'écrit rien dans rule_changes, affiche seulement le rapport.")
    args = parser.parse_args()

    hyp_key = args.hypothesis
    cfg = HYPOTHESES[hyp_key]
    candidates = list(cfg["candidates"].values())

    print(f"=== Cycle d'évolution {hyp_key} — {len(candidates)} candidat(s), actifs={cfg['assets']} ===")
    print(f"Découpage : ENTRAÎNEMENT < {CUTOFF} | VALIDATION >= {CUTOFF}\n")

    def train_fn(c: CandidateSpec) -> TrainingResult:
        r = _run_pooled(hyp_key, c, split_index=0)
        esp = f"{r.mean_r:.4f}R" if r.mean_r is not None else "N/A"
        print(f"  [entraînement] {c.name}: n={r.n_trades} espérance={esp}")
        return r

    def validation_fn(c: CandidateSpec) -> TrainingResult:
        r = _run_pooled(hyp_key, c, split_index=1)
        esp = f"{r.mean_r:.4f}R" if r.mean_r is not None else "N/A"
        print(f"  [validation] {c.name}: n={r.n_trades} espérance={esp}")
        return r

    def notify_fn(row: dict) -> None:
        # Point 5 (29/08/2026) : une notification PAR proposition écrite,
        # avec le chiffre qui la motive (constat_stat) — jamais un simple
        # "un changement a été proposé" muet. Best-effort : un échec
        # d'envoi Telegram ne doit jamais faire échouer un cycle déjà
        # écrit en base (la ligne 'propose' existe déjà, relisible via
        # /etat ou une requête directe même sans notification).
        try:
            config = load_config()
        except Exception:
            print(f"  [notification] TELEGRAM_BOT_TOKEN/CHAT_ID absents — proposition {row['variable']} non notifiée (relisible dans rule_changes).")
            return
        message = (
            f"Nouvelle proposition d'évolution — {row['variable']}\n"
            f"Constat : {row['constat_stat']}\n"
            f"Ajustement proposé : {row['ajustement_propose']}\n"
            f"Statut : propose (jamais appliqué automatiquement — /etat pour valider ou rejeter)."
        )
        if not send_notification(config.telegram_bot_token, config.telegram_chat_id, message):
            print(f"  [notification] Échec de l'envoi Telegram pour {row['variable']} (proposition déjà écrite en base).")

    report = run_evolution_cycle(
        hyp_key, candidates, train_fn, validation_fn,
        min_trades_train=PHASE_B_MIN_TRADES_BACKTEST, min_trades_validation=PHASE_A_MIN_TRADES_BACKTEST,
        db_path=args.db, persist=not args.dry_run, notify_fn=None if args.dry_run else notify_fn,
    )

    print(f"\nSélection : {report.selection.reason}")
    if report.validation is not None:
        print(f"Validation : {report.validation.reason} -> {'PASS' if report.validation.passed else 'FAIL'}")
    if report.applied_rule_change_ids:
        mode = "DRY-RUN (rien écrit)" if args.dry_run else "ÉCRIT (rule_changes.statut='propose' — jamais appliqué automatiquement, voir docs/DECISIONS.md point 5)"
        print(f"Résultat : {mode} — ids {report.applied_rule_change_ids}")
    else:
        print("Résultat : aucun changement proposé.")


if __name__ == "__main__":
    main()
