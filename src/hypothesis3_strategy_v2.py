"""
hypothesis3_strategy_v2.py — Hypothèse #3, refonte L3 (29/08/2026,
pré-enregistré dans docs/HYPOTHESES.md AVANT tout calcul). Pullback en
tendance : régime structurel + jambe d'impulsion RÉUTILISÉS TELS QUELS
(`ict_strategy._find_regime_and_leg`, aucune nouvelle logique de
régime), entrée = retour du prix sur le niveau de retracement de cette
jambe PUIS reprise confirmée sur `CONFIRMATION_BARS` bougies
consécutives dans le sens de la tendance. Remplace
`hypothesis3_strategy.py` (wrapper autour de `trend_strategy.
evaluate_entry`, archivé — voir archive/).

Risque identifié par Ismaël avant construction (biais de sélection) :
les mouvements SANS pullback sont les plus forts, donc les plus
profitables, et cette logique les élimine par construction. Garde-fou
obligatoire (point 7 du pré-enregistrement) — À APPLIQUER AU MOMENT DE
LA CALIBRATION/CONFIRMATION (points F/G/H), PAS ICI : l'espérance devra
être rapportée À LA FOIS par signal détecté (régime+pullback réunis,
ordres non remplis comptés 0R) ET par trade exécuté, jamais l'une sans
l'autre. Ce module se contente de produire le signal ; le calcul des
deux espérances est une responsabilité de la couche d'analyse, pas de
la stratégie elle-même (même séparation que partout ailleurs dans le
projet — logique pure vs. analyse statistique).
"""

from typing import List, Optional

from src.ict_strategy import _find_regime_and_leg
from src.market_data import Candle, compute_atr
from src.trend_strategy import TrendSignal, compute_tp_levels

ATR_PERIOD = 14  # FIGÉ
TP1_R_MULTIPLE = 1.0  # FIGÉ, structure de sortie standard §2.10
TP2_R_MULTIPLE = 2.0  # FIGÉ

# Variables AJUSTÉES (pré-enregistrement 29/08/2026, docs/HYPOTHESES.md) —
# valeurs par défaut = point médian de la grille, remplacées par
# hypothesis_params.apply_overrides une fois la calibration validée.
RETRACEMENT_RATIO = 0.5
CONFIRMATION_BARS = 2
STOP_BUFFER_ATR = 0.5

OVERRIDABLE = ["RETRACEMENT_RATIO", "CONFIRMATION_BARS", "STOP_BUFFER_ATR"]


def _retracement_level(direction: str, swing_low: float, swing_high: float, ratio: float) -> float:
    span = swing_high - swing_low
    return swing_high - ratio * span if direction == "long" else swing_low + ratio * span


def _touched_level(candle: Candle, direction: str, level: float) -> bool:
    """Vrai si `candle` a atteint ou dépassé `level` dans le sens du
    retracement — pour `long`, un creux (`low`) atteignant ou passant
    sous `level` ; pour `short`, un sommet (`high`) atteignant ou
    passant au-dessus."""
    return candle.low <= level if direction == "long" else candle.high >= level


def _resumption_confirmed(candles: List[Candle], direction: str, start: int, end: int) -> bool:
    """True si les clôtures de `candles[start:end+1]` progressent
    STRICTEMENT dans le sens de `direction` à chaque bougie (reprise
    nette, pas un simple rebond isolé)."""
    closes = [c.close for c in candles[start:end + 1]]
    if len(closes) < 2:
        return False
    pairs = zip(closes, closes[1:])
    return all(b > a for a, b in pairs) if direction == "long" else all(b < a for a, b in pairs)


def evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    """Point d'entrée unique. Ne lève jamais d'exception (fail-safe,
    invariant #7, même patron que trend_strategy.evaluate_entry)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: Optional[List[Candle]]) -> Optional[TrendSignal]:
    if not candles:
        return None
    last_index = len(candles) - 1
    n = CONFIRMATION_BARS
    if last_index < n:
        return None

    regime_and_leg = _find_regime_and_leg(candles)
    if regime_and_leg is None:
        return None
    direction, swing_low, swing_high, _current_close = regime_and_leg

    level = _retracement_level(direction, swing_low, swing_high, RETRACEMENT_RATIO)

    # Événement ponctuel, jamais un état persistant : le creux/sommet de
    # retracement doit se situer EXACTEMENT `CONFIRMATION_BARS` bougies
    # avant la bougie courante, jamais "n'importe où avant" (sinon le
    # signal refirait à chaque nouvelle bougie tant que la reprise se
    # poursuit).
    touch_idx = last_index - n
    if touch_idx < 0 or not _touched_level(candles[touch_idx], direction, level):
        return None
    if not _resumption_confirmed(candles, direction, touch_idx, last_index):
        return None

    # ATR(14) exige moins d'historique (15 bougies) que le régime/jambe
    # structurels déjà validés ci-dessus (25 minimum,
    # ict_strategy._find_regime_and_leg) : toujours disponible à ce
    # stade, jamais None — pas de vérification redondante sur une
    # branche inatteignable (même constat que pour H1/L1).
    atr = compute_atr(candles, ATR_PERIOD)

    entry_price = candles[-1].close
    if direction == "long":
        stop_price = level - STOP_BUFFER_ATR * atr
    else:
        stop_price = level + STOP_BUFFER_ATR * atr
    tp1, tp2 = compute_tp_levels(direction, entry_price, stop_price, TP1_R_MULTIPLE, TP2_R_MULTIPLE)
    return TrendSignal(asset=asset, direction=direction, entry_price=entry_price, stop_price=stop_price, tp1=tp1, tp2=tp2)
