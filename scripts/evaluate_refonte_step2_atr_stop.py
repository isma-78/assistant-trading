"""
evaluate_refonte_step2_atr_stop.py — Phase 2, étape 2 (priorité #2) du
chantier de refonte H2-H5 (docs/HYPOTHESES.md, 25/08/2026). Branche A
(H3, H5). Résolution retenue à l'étape 1 : MINUTE_15 pour les deux
(aucune résolution alternative n'a qualifié — voir logs/refonte_step1.log).

Remplace le stop actuel (borne opposée du canal pour H3, borne opposée
de la jambe structurelle pour H5) par un multiple d'ATR(14) — le signal
d'entrée (déclencheur, régime) reste INCHANGÉ, seul le stop change,
donc R change (et donc TP1/TP2 en prix, inchangés en multiples de R).

Calibration DÉTERMINISTE (pas une grille de candidats) : à partir du
coût réel mesuré en Phase 1 (`logs/evaluate_zero_cost_diagnostic.log` —
coût en R × stop moyen actuel = coût en unités de prix, approximativement
indépendant de la définition du stop), calcule le multiple d'ATR minimal
nécessaire pour que le ratio coût/R descende sous 5% pour CHAQUE actif,
retient le MAXIMUM (le cas le plus pénalisé, garantit <5% partout avec
UN SEUL multiple partagé — même convention "un seul jeu de valeurs par
hypothèse" que le reste du projet), arrondi au 0,5 supérieur (valeur a
priori lisible).

Un seul candidat par hypothèse (le multiple calibré) — correction m=1
(quantile normal unilatéral 95%, même formule que le candidat squeeze
breakout). n >= PHASE_B_MIN_TRADES_BACKTEST ET (moyenne − z×SE) > 0 sur
l'ENTRAÎNEMENT seul. AUCUNE consultation de la validation ici (réservée
au candidat FINAL après l'étape 3).

Aucune écriture DB, aucun appel réseau.

Usage :
    python scripts/evaluate_refonte_step2_atr_stop.py
"""

import json
import math
import statistics
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ict_strategy as ict
from src import hypothesis5_strategy as h5_mod
from src.asset_whitelist import ASSET_WHITELIST
from src.backtest_engine import HistoricalBar, bar_from_raw, replay_hypothesis
from src.confidence_scorer import PHASE_A_MIN_TRADES_BACKTEST, PHASE_B_MIN_TRADES_BACKTEST
from src.market_data import Candle, compute_atr
from src.risk_engine import RiskCaps, RiskEngine
from src.trend_strategy import TrendSignal, compute_donchian_channel, compute_regime, compute_tp_levels

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
ALL_ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]

CUTOFF = "2025-12-01T00:00:00"
ENVELOPE_INITIAL = 500.0
CONFIDENCE_THRESHOLD = 0.75
RISK_PERCENT_DEFAULT = 2.0
RISK_PERCENT_BOOSTED = 4.0

# Coût mesuré en Phase 1 (coût_R, stop_moyen_actuel) par (hypothèse, actif)
# — recopié depuis logs/evaluate_zero_cost_diagnostic.log, PAS recalculé
# ici (mesure déjà faite et journalisée dans docs/DECISIONS.md).
PHASE1_COST = {
    "H3": {
        "GOLD": (0.0692, 31.214774), "US100": (0.0625, 178.520366), "US30": (0.0471, 253.077703),
        "EURUSD": (0.1328, 0.003198), "GBPUSD": (0.1793, 0.004008), "USDJPY": (0.1368, 0.596645),
        "BTCUSD": (0.1936, 1313.774776), "ETHUSD": (0.1666, 63.102151),
    },
    "H5": {
        "GOLD": (0.1368, 16.665140), "US100": (0.1031, 97.855263), "US30": (0.0792, 128.623450),
        "EURUSD": (0.1936, 0.001887), "GBPUSD": (0.3205, 0.002351), "USDJPY": (0.2145, 0.328428),
        "BTCUSD": (0.3286, 737.308154), "ETHUSD": (0.3170, 36.207525),
    },
}
TARGET_COST_RATIO = 0.05

_bar_cache: Dict[str, List[HistoricalBar]] = {}


def _load_bars(epic: str) -> List[HistoricalBar]:
    if epic in _bar_cache:
        return _bar_cache[epic]
    path = HISTORICAL_DIR / f"{epic}_MINUTE_15.json"
    raw_points = json.loads(path.read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw_points) if b is not None]
    _bar_cache[epic] = bars
    return bars


def _split(bars: List[HistoricalBar], cutoff: str) -> tuple:
    return [b for b in bars if b.time_utc < cutoff], [b for b in bars if b.time_utc >= cutoff]


def _typical_atr(candles: List[Candle], sample_every: int = 5000, period: int = 14) -> Optional[float]:
    """ATR(14) échantillonné à intervalles réguliers sur toute la série
    (pas une seule valeur en fin de série, qui sous-pondérerait les
    régimes de volatilité passés) — moyenne des échantillons."""
    samples = []
    i = period + 1
    while i < len(candles):
        atr = compute_atr(candles[:i], period=period)
        if atr is not None and atr > 0:
            samples.append(atr)
        i += sample_every
    return statistics.fmean(samples) if samples else None


def _calibrate_atr_multiple(hyp_key: str, resolution_bars: Dict[str, List[HistoricalBar]]) -> float:
    required = []
    for asset in ALL_ASSETS:
        cost_r, stop_moyen = PHASE1_COST[hyp_key][asset]
        cost_price = cost_r * stop_moyen
        needed_stop = cost_price / TARGET_COST_RATIO
        candles = [b.to_candle() for b in resolution_bars[asset]]
        atr = _typical_atr(candles)
        if atr is None or atr <= 0:
            continue
        required.append(needed_stop / atr)
    worst = max(required)
    # arrondi au 0.5 supérieur — valeur a priori lisible
    return math.ceil(worst * 2) / 2.0


def _make_risk_engine() -> RiskEngine:
    caps = RiskCaps(risk_percent_default=RISK_PERCENT_DEFAULT, risk_percent_boosted=RISK_PERCENT_BOOSTED, envelope_initial=ENVELOPE_INITIAL)
    return RiskEngine(caps=caps, whitelist=ASSET_WHITELIST)


def _one_sided_z_95() -> float:
    alpha = 0.05
    p = 1 - alpha
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)


# --- H3 : régime MA200 + rupture Donchian(20) INCHANGÉ, stop = ATR --------

def make_h3_atr_entry(atr_multiple: float):
    def _entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
        try:
            regime = compute_regime(candles)
            if regime is None:
                return None
            channel = compute_donchian_channel(candles)
            if channel is None:
                return None
            highest, lowest = channel
            current_close = candles[-1].close
            atr = compute_atr(candles, period=14)
            if atr is None or atr <= 0:
                return None

            if regime == "long" and current_close > highest:
                stop_price = current_close - atr_multiple * atr
            elif regime == "short" and current_close < lowest:
                stop_price = current_close + atr_multiple * atr
            else:
                return None

            tp1, tp2 = compute_tp_levels(regime, current_close, stop_price, 1.0, 2.0)
            return TrendSignal(asset=asset, direction=regime, entry_price=current_close, stop_price=stop_price, tp1=tp1, tp2=tp2)
        except Exception:
            return None
    return _entry


# --- H5 : régime structurel + RSI franchissant 50 INCHANGÉ, stop = ATR ----

def make_h5_atr_entry(atr_multiple: float):
    def _entry(asset: str, candles: List[Candle]) -> Optional[h5_mod.Hypothesis5Signal]:
        try:
            structural_signal = ict.compute_structural_entry(asset, candles)
            if structural_signal is None:
                return None
            if not h5_mod._rsi_just_crossed_threshold(candles, structural_signal.direction):
                return None
            atr = compute_atr(candles, period=14)
            if atr is None or atr <= 0:
                return None
            if structural_signal.direction == "long":
                stop_price = structural_signal.entry_price - atr_multiple * atr
            else:
                stop_price = structural_signal.entry_price + atr_multiple * atr
            tp1, tp2 = h5_mod._compute_tp_levels(structural_signal.direction, structural_signal.entry_price, stop_price)
            return h5_mod.Hypothesis5Signal(
                asset=asset, direction=structural_signal.direction, entry_price=structural_signal.entry_price,
                stop_price=stop_price, tp1=tp1, tp2=tp2,
            )
        except Exception:
            return None
    return _entry


HYPOTHESES = {
    "H3": {"require_regime": True, "make_entry": make_h3_atr_entry},
    "H5": {"require_regime": False, "make_entry": make_h5_atr_entry},
}


def _run_train(hyp_key: str, entry_fn) -> tuple:
    cfg = HYPOTHESES[hyp_key]
    risk_engine = _make_risk_engine()
    all_r: List[float] = []
    per_asset = {}
    for asset in ALL_ASSETS:
        train, _ = _split(_load_bars(asset), CUTOFF)
        confirming_bars = None
        if cfg["require_regime"]:
            us30_train, _ = _split(_load_bars("US30"), CUTOFF)
            us100_train, _ = _split(_load_bars("US100"), CUTOFF)
            confirming_bars = {"US30": us30_train, "US100": us100_train}
        result = replay_hypothesis(
            asset, train, entry_fn, risk_engine, ASSET_WHITELIST, ENVELOPE_INITIAL, CONFIDENCE_THRESHOLD,
            require_regime_confirmation=cfg["require_regime"], confirming_bars=confirming_bars,
        )
        r_values = [t.r_multiple_total for t in result.trades]
        all_r.extend(r_values)
        per_asset[asset] = (len(r_values), statistics.fmean(r_values) if r_values else None)
    n = len(all_r)
    mean = statistics.fmean(all_r) if all_r else None
    stdev = statistics.stdev(all_r) if len(all_r) >= 2 else None
    return n, mean, stdev, per_asset


def main() -> None:
    print("Phase 2, étape 2 — stop calibré ATR (H3, H5 — branche A). Résolution : MINUTE_15 (retenue à l'étape 1).\n")
    z = _one_sided_z_95()

    train_bars = {a: _split(_load_bars(a), CUTOFF)[0] for a in ALL_ASSETS}

    for hyp_key, cfg in HYPOTHESES.items():
        multiple = _calibrate_atr_multiple(hyp_key, train_bars)
        print(f"=== {hyp_key} — multiple d'ATR calibré : {multiple} ===")
        entry_fn = cfg["make_entry"](multiple)
        n, mean, stdev, per_asset = _run_train(hyp_key, entry_fn)
        if n >= 2 and mean is not None and stdev is not None:
            se = stdev / math.sqrt(n)
            lower_bound = mean - z * se
        else:
            lower_bound = None
        mean_str = f"{mean:.4f}R" if mean is not None else "N/A"
        lb_str = f"{lower_bound:.4f}R" if lower_bound is not None else "N/A"
        qualifies = n >= PHASE_B_MIN_TRADES_BACKTEST and lower_bound is not None and lower_bound > 0
        print(f"  n={n} moyenne={mean_str} borne_basse={lb_str} {'[QUALIFIE]' if qualifies else ''}")
        for asset, (an, ae) in per_asset.items():
            ae_str = f"{ae:.4f}R" if ae is not None else "N/A"
            print(f"      {asset}: n={an} espérance={ae_str}")
        print()

    print("Terminé.")


if __name__ == "__main__":
    main()
