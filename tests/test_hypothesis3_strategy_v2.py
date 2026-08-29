"""
tests/test_hypothesis3_strategy_v2.py — H3/L3 (refonte 29/08/2026, voir
docs/HYPOTHESES.md). Régime/jambe réutilisés tels quels depuis
ict_strategy._find_regime_and_leg — ces tests ne re-testent PAS cette
fonction (déjà 100% couverte dans test_ict_strategy.py), seulement la
logique de retracement/reprise ajoutée par ce module.
"""

import pytest

from src.hypothesis3_strategy_v2 import (
    ATR_PERIOD,
    CONFIRMATION_BARS,
    RETRACEMENT_RATIO,
    STOP_BUFFER_ATR,
    _resumption_confirmed,
    _retracement_level,
    _touched_level,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, h, l, c):
    return Candle(time_utc=str(i), open=c, high=h, low=l, close=c)


def _pullback_series(direction="long"):
    """Régime + jambe établis (swing_low=80, swing_high=140), puis
    repli à exactement RETRACEMENT_RATIO(0.5) de la jambe (niveau=110),
    puis reprise sur CONFIRMATION_BARS(2) bougies concluant AU-DELÀ du
    swing d'origine (régime "long" confirmé fraîchement à la bougie
    courante, comme l'exige ict_strategy.compute_structural_regime).
    Construction vérifiée numériquement pendant le développement (voir
    docs/DECISIONS.md) — deux bougies de repli à low IDENTIQUE (108) pour
    ne jamais créer de swing fractal parasite (comparaison stricte)."""
    baseline = [_c(i, 100, 100, 100) for i in range(200)]
    recent, t = [], 200
    for _ in range(3):
        recent.append(_c(t, 100, 100, 100)); t += 1
    if direction == "long":
        recent.append(_c(t, 92, 80, 85)); t += 1       # swing low 80
        for _ in range(2):
            recent.append(_c(t, 100, 90, 95)); t += 1
        recent.append(_c(t, 140, 120, 135)); t += 1     # swing high 140
        for _ in range(2):
            recent.append(_c(t, 120, 100, 110)); t += 1
        for _ in range(2):
            recent.append(_c(t, 125, 115, 120)); t += 1
        recent.append(_c(t, 115, 108, 110)); t += 1     # touche le niveau 110
        recent.append(_c(t, 115, 108, 108)); t += 1     # tie (pas de swing parasite)
        recent.append(_c(t, 130, 108, 125)); t += 1     # reprise 1/2
        recent.append(_c(t, 150, 125, 145)); t += 1     # reprise 2/2, close > 140
    else:
        recent.append(_c(t, 120, 108, 115)); t += 1     # swing high 120 (symétrique)
        for _ in range(2):
            recent.append(_c(t, 110, 100, 105)); t += 1
        recent.append(_c(t, 80, 60, 65)); t += 1        # swing low 60
        for _ in range(2):
            recent.append(_c(t, 100, 80, 90)); t += 1
        for _ in range(2):
            recent.append(_c(t, 85, 75, 80)); t += 1
        recent.append(_c(t, 92, 85, 90)); t += 1        # touche le niveau 90
        recent.append(_c(t, 92, 85, 92)); t += 1        # tie
        recent.append(_c(t, 92, 70, 75)); t += 1         # reprise 1/2
        recent.append(_c(t, 70, 50, 55)); t += 1         # reprise 2/2, close < 60
    return baseline + recent


# ---------------------------------------------------------------------------
# _retracement_level / _touched_level / _resumption_confirmed
# ---------------------------------------------------------------------------

def test_retracement_level_long_at_half_ratio():
    assert _retracement_level("long", swing_low=80.0, swing_high=140.0, ratio=0.5) == 110.0


def test_retracement_level_short_at_half_ratio():
    assert _retracement_level("short", swing_low=60.0, swing_high=120.0, ratio=0.5) == 90.0


def test_touched_level_long_true_when_low_reaches_level():
    assert _touched_level(_c(0, 115, 108, 110), "long", 110.0) is True
    assert _touched_level(_c(0, 115, 111, 112), "long", 110.0) is False


def test_touched_level_short_true_when_high_reaches_level():
    assert _touched_level(_c(0, 92, 85, 90), "short", 90.0) is True
    assert _touched_level(_c(0, 89, 80, 85), "short", 90.0) is False


def test_resumption_confirmed_requires_strictly_increasing_closes_long():
    candles = [_c(0, 1, 1, 100), _c(1, 1, 1, 105), _c(2, 1, 1, 110)]
    assert _resumption_confirmed(candles, "long", 0, 2) is True
    candles_flat = [_c(0, 1, 1, 100), _c(1, 1, 1, 100), _c(2, 1, 1, 110)]
    assert _resumption_confirmed(candles_flat, "long", 0, 2) is False


def test_resumption_confirmed_requires_strictly_decreasing_closes_short():
    candles = [_c(0, 1, 1, 100), _c(1, 1, 1, 95), _c(2, 1, 1, 90)]
    assert _resumption_confirmed(candles, "short", 0, 2) is True


def test_resumption_confirmed_false_when_window_too_short():
    assert _resumption_confirmed([_c(0, 1, 1, 100)], "long", 0, 0) is False


# ---------------------------------------------------------------------------
# evaluate_entry — bout en bout
# ---------------------------------------------------------------------------

def test_evaluate_entry_long_pullback_then_resumption():
    candles = _pullback_series("long")
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == candles[-1].close
    assert signal.stop_price == pytest.approx(110.0 - STOP_BUFFER_ATR * _atr_of(candles))
    assert signal.tp1 is not None and signal.tp2 is not None


def _atr_of(candles):
    from src.market_data import compute_atr
    return compute_atr(candles, ATR_PERIOD)


def test_evaluate_entry_short_pullback_then_resumption():
    candles = _pullback_series("short")
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price > signal.entry_price


def test_evaluate_entry_none_one_bar_before_full_resumption():
    candles = _pullback_series("long")
    assert evaluate_entry("EURUSD", candles[:-1]) is None


def test_evaluate_entry_none_when_touch_bar_too_far_from_current():
    # Une bougie de reprise supplémentaire au milieu décale le repli hors
    # de la fenêtre "exactement CONFIRMATION_BARS avant" -> plus de signal
    # pour cette paire régime/repli (événement ponctuel, jamais un état
    # persistant).
    candles = _pullback_series("long")
    extra = candles[:-1] + [_c(9999, 155, 145, 150)] + candles[-1:]
    assert evaluate_entry("EURUSD", extra) is None


def test_evaluate_entry_none_when_no_touch_at_expected_index():
    candles = _pullback_series("long")
    # Remplace la bougie de repli (qui touche le niveau) par une bougie
    # qui reste largement au-dessus du niveau de retracement.
    touch_idx = len(candles) - 1 - CONFIRMATION_BARS
    candles = list(candles)
    candles[touch_idx] = _c(touch_idx, 135, 130, 132)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_none_when_no_regime():
    flat = [_c(i, 100, 100, 100) for i in range(30)]
    assert evaluate_entry("EURUSD", flat) is None


def test_evaluate_entry_none_when_too_short_series():
    assert evaluate_entry("EURUSD", [_c(0, 101, 99, 100)]) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", []) is None
    assert evaluate_entry("EURUSD", None) is None


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal():
    class _Broken:
        def __len__(self):
            return 100

        def __getitem__(self, item):
            raise RuntimeError("boom")

    assert evaluate_entry("EURUSD", _Broken()) is None


def test_module_constants_within_preregistered_grid():
    assert RETRACEMENT_RATIO in (0.382, 0.5, 0.618)
    assert CONFIRMATION_BARS in (1, 2, 3)
    assert STOP_BUFFER_ATR in (0.5, 1.0)
