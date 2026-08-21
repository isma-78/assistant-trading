import pytest

from src.ict_strategy import (
    FRACTAL_K,
    RECENT_WINDOW,
    classify_structure_break,
    compute_fibonacci_zone,
    evaluate_entry,
    find_confirmed_swings,
    find_fvgs,
    _evaluate_entry,
    _find_latest_valid_leg,
)
from src.market_data import Candle
from src.trend_strategy import MA_PERIOD


def _c(i, high, low, close):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close)


def _flat(n, value=100.0, start=0):
    return [_c(start + i, value, value, value) for i in range(n)]


# ---------------------------------------------------------------------------
# find_confirmed_swings
# ---------------------------------------------------------------------------

def test_find_confirmed_swings_detects_isolated_high_and_low():
    highs = [100, 100, 100, 140, 100, 100, 100]
    lows = [100, 100, 100, 90, 100, 100, 100]
    candles = [_c(i, highs[i], lows[i], 100.0) for i in range(7)]
    swing_highs, swing_lows = find_confirmed_swings(candles, k=2)
    assert swing_highs == [(3, 140)]
    assert swing_lows == [(3, 90)]


def test_find_confirmed_swings_flat_series_has_no_swings():
    candles = _flat(10)
    swing_highs, swing_lows = find_confirmed_swings(candles, k=2)
    assert swing_highs == []
    assert swing_lows == []


def test_find_confirmed_swings_edges_never_confirmed():
    # Un extremum sur les k premières/dernières bougies ne peut jamais
    # être confirmé (pas assez de contexte des deux côtés).
    highs = [200, 100, 100, 100, 100]
    lows = [100, 100, 100, 100, 50]
    candles = [_c(i, highs[i], lows[i], 100.0) for i in range(5)]
    swing_highs, swing_lows = find_confirmed_swings(candles, k=2)
    assert swing_highs == []
    assert swing_lows == []


# ---------------------------------------------------------------------------
# find_fvgs
# ---------------------------------------------------------------------------

def test_find_fvgs_detects_bullish_gap():
    candles = [_c(0, 100, 95, 98), _c(1, 105, 99, 102), _c(2, 115, 110, 112)]
    fvgs = find_fvgs(candles)
    assert fvgs == [("long", 100, 110)]


def test_find_fvgs_detects_bearish_gap():
    candles = [_c(0, 110, 105, 108), _c(1, 100, 95, 98), _c(2, 95, 90, 92)]
    fvgs = find_fvgs(candles)
    assert fvgs == [("short", 95, 105)]


def test_find_fvgs_none_when_no_gap():
    candles = [_c(0, 100, 95, 98), _c(1, 101, 96, 99), _c(2, 100, 94, 97)]
    assert find_fvgs(candles) == []


def test_find_fvgs_too_few_candles():
    assert find_fvgs([_c(0, 100, 95, 98), _c(1, 101, 96, 99)]) == []


# ---------------------------------------------------------------------------
# compute_fibonacci_zone
# ---------------------------------------------------------------------------

def test_compute_fibonacci_zone_long():
    low, high = compute_fibonacci_zone("long", swing_low=90, swing_high=140)
    assert low == pytest.approx(140 - 0.786 * 50)
    assert high == pytest.approx(140 - 0.618 * 50)
    assert low < high


def test_compute_fibonacci_zone_short():
    low, high = compute_fibonacci_zone("short", swing_low=90, swing_high=140)
    assert low == pytest.approx(90 + 0.618 * 50)
    assert high == pytest.approx(90 + 0.786 * 50)


def test_compute_fibonacci_zone_rejects_inverted_swing():
    with pytest.raises(ValueError):
        compute_fibonacci_zone("long", swing_low=140, swing_high=90)


def test_compute_fibonacci_zone_rejects_equal_swing():
    with pytest.raises(ValueError):
        compute_fibonacci_zone("long", swing_low=100, swing_high=100)


def test_compute_fibonacci_zone_unknown_direction():
    with pytest.raises(ValueError):
        compute_fibonacci_zone("sideways", swing_low=90, swing_high=140)


# ---------------------------------------------------------------------------
# classify_structure_break
# ---------------------------------------------------------------------------

def test_classify_structure_break_bos_long():
    assert classify_structure_break(150, [(5, 140)], [(3, 90)], "long") == "BOS"


def test_classify_structure_break_choch_long():
    assert classify_structure_break(80, [(5, 140)], [(3, 90)], "long") == "CHoCH"


def test_classify_structure_break_bos_short():
    assert classify_structure_break(80, [(5, 140)], [(3, 90)], "short") == "BOS"


def test_classify_structure_break_choch_short():
    assert classify_structure_break(150, [(5, 140)], [(3, 90)], "short") == "CHoCH"


def test_classify_structure_break_none_when_inside_range():
    assert classify_structure_break(100, [(5, 140)], [(3, 90)], "long") is None


def test_classify_structure_break_none_when_no_swings():
    assert classify_structure_break(100, [], [], "long") is None


def test_classify_structure_break_unknown_bias():
    with pytest.raises(ValueError):
        classify_structure_break(100, [], [], "sideways")


# ---------------------------------------------------------------------------
# _find_latest_valid_leg
# ---------------------------------------------------------------------------

def test_find_latest_valid_leg_long():
    swing_highs = [(2, 120), (10, 140)]
    swing_lows = [(1, 95), (8, 90)]
    assert _find_latest_valid_leg(swing_highs, swing_lows, "long") == (90, 140)


def test_find_latest_valid_leg_long_no_high_after_low():
    swing_highs = [(2, 120)]
    swing_lows = [(8, 90)]
    assert _find_latest_valid_leg(swing_highs, swing_lows, "long") is None


def test_find_latest_valid_leg_short():
    swing_highs = [(1, 140), (8, 130)]
    swing_lows = [(2, 100), (10, 90)]
    assert _find_latest_valid_leg(swing_highs, swing_lows, "short") == (90, 130)


def test_find_latest_valid_leg_short_no_low_after_high():
    swing_highs = [(8, 130)]
    swing_lows = [(2, 100)]
    assert _find_latest_valid_leg(swing_highs, swing_lows, "short") is None


def test_find_latest_valid_leg_no_swings_at_all():
    assert _find_latest_valid_leg([], [], "long") is None
    assert _find_latest_valid_leg([(1, 100)], [], "long") is None
    assert _find_latest_valid_leg([], [(1, 100)], "long") is None


def test_find_latest_valid_leg_unknown_direction():
    with pytest.raises(ValueError):
        _find_latest_valid_leg([(1, 100)], [(1, 90)], "sideways")


# ---------------------------------------------------------------------------
# evaluate_entry / _evaluate_entry — bout en bout
# ---------------------------------------------------------------------------

# Bougies "récentes" (25) construites à la main pour produire, en régime
# haussier : un swing bas confirmé (idx8=90), un swing haut confirmé
# (idx16=140), une zone de Fibonacci [100.7, 109.1], un FVG haussier en
# fin de fenêtre (idx22->idx24, zone [98,105]) qui chevauche cette zone,
# et une clôture finale (idx24=105) dans la zone. Voir le raisonnement
# complet dans le message de conception (docs/HYPOTHESES.md, 21/08/2026).
_RECENT_LONG = [
    (100, 100, 100), (100, 100, 100), (100, 100, 100), (100, 100, 100),
    (100, 100, 100), (100, 100, 100), (100, 100, 100), (100, 100, 100),  # idx0-7
    (97, 90, 93),      # idx8 : swing bas
    (99, 92, 95), (101, 94, 97),                                        # idx9-10 (chevauche idx8, pas de gap)
    (104, 96, 99), (108, 99, 103), (113, 103, 108),                     # idx11-13
    (119, 108, 114), (126, 114, 121),                                   # idx14-15
    (140, 121, 135),  # idx16 : swing haut (nette rupture, aucun gap avec idx14/15)
    (134, 122, 128), (128, 116, 122),                                   # idx17-18
    (122, 110, 116), (117, 105, 110), (112, 99, 105),                   # idx19-21
    (98, 95, 97),      # idx22 : C1 du FVG, PAS un swing (idx23 a un low plus bas)
    (100, 90, 94),     # idx23 : C2 du FVG
    (109, 103, 105),   # idx24 : C3 du FVG, clôture courante, dans la zone
]


def _build_candles(recent_triples, baseline_n=200, baseline_value=100.0):
    baseline = [_c(i, baseline_value, baseline_value, baseline_value) for i in range(baseline_n)]
    recent = [_c(baseline_n + i, h, l, c) for i, (h, l, c) in enumerate(recent_triples)]
    return baseline + recent


def test_evaluate_entry_long_full_confluence():
    candles = _build_candles(_RECENT_LONG)
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(105.0)
    assert signal.stop_price == pytest.approx(90.0)  # swing bas de la jambe


def test_evaluate_entry_short_full_confluence():
    # Symétrique exact de _RECENT_LONG (miroir haut/bas), en régime baissier.
    mirrored = [(200 - low, 200 - high, 200 - close) for (high, low, close) in _RECENT_LONG]
    candles = _build_candles(mirrored)
    signal = evaluate_entry("EURUSD", candles)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price == pytest.approx(110.0)  # swing haut de la jambe (200-90)


def test_evaluate_entry_insufficient_history():
    candles = _build_candles(_RECENT_LONG, baseline_n=50)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_flat_regime_returns_none():
    candles = _build_candles([(100, 100, 100)] * 25)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_no_valid_leg_returns_none():
    # Régime haussier net, mais aucun swing dans la fenêtre récente.
    recent = [(105 + i * 0.1, 100 + i * 0.1, 102 + i * 0.1) for i in range(25)]
    candles = _build_candles(recent)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_price_outside_zone_returns_none():
    # Même jambe swing bas/haut que le cas long, mais la clôture finale
    # reste loin au-dessus de la zone de Fibonacci (pas de retracement).
    recent = list(_RECENT_LONG)
    recent[-1] = (145, 138, 138)  # clôture bien au-dessus de la zone [100.7, 109.1]
    candles = _build_candles(recent)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_no_fvg_overlap_returns_none():
    recent = list(_RECENT_LONG)
    # Supprime le FVG en rendant idx22/idx24 non-disjoints (pas de gap) :
    # low(idx24) <= high(idx22), la même jambe/zone reste valide.
    recent[24] = (109, 95, 105)
    candles = _build_candles(recent)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", None) is None


def test_recent_window_and_fractal_k_constants():
    assert RECENT_WINDOW == 20
    assert FRACTAL_K == 2
