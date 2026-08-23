import pytest

from src.ict_strategy import (
    FRACTAL_K,
    RECENT_WINDOW,
    classify_structure_break,
    compute_fibonacci_zone,
    compute_structural_regime,
    evaluate_entry,
    find_confirmed_swings,
    find_fvgs,
    _evaluate_entry,
    _find_latest_valid_leg,
)
from src.market_data import Candle


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
# compute_structural_regime (bascule du 23/08/2026 — remplace compute_regime/
# MA200 comme régime de fond de l'Hypothèse #2, voir docs/DECISIONS.md)
# ---------------------------------------------------------------------------

def test_compute_structural_regime_long_on_break_above_last_high():
    # Clôture au-dessus du dernier plus haut confirmé -> BOS avec bias
    # "long" -> régime haussier.
    assert compute_structural_regime([(5, 140)], [(3, 90)], current_close=150) == "long"


def test_compute_structural_regime_short_on_break_below_last_low():
    # Clôture en dessous du dernier plus bas confirmé -> BOS avec bias
    # "short" -> régime baissier.
    assert compute_structural_regime([(5, 140)], [(3, 90)], current_close=80) == "short"


def test_compute_structural_regime_none_when_inside_range():
    # Clôture entre le dernier plus bas et le dernier plus haut : ni
    # cassure haussière ni baissière -> pas de régime tranché.
    assert compute_structural_regime([(5, 140)], [(3, 90)], current_close=100) is None


def test_compute_structural_regime_none_without_swing_highs():
    assert compute_structural_regime([], [(3, 90)], current_close=150) is None


def test_compute_structural_regime_none_without_swing_lows():
    assert compute_structural_regime([(5, 140)], [], current_close=150) is None


def test_compute_structural_regime_none_without_any_swings():
    assert compute_structural_regime([], [], current_close=100) is None


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

# Bougies "récentes" (25) construites à la main, VALIDÉES en exécutant le
# code réel (pas seulement calculées à la main — voir docs/DECISIONS.md,
# 23/08/2026, pour la méthode). Doivent produire, avec le régime
# STRUCTUREL (bascule du 23/08/2026, remplace MA200) :
# - idx6 : swing bas confirmé (90) — ancre de la jambe, DERNIER swing bas
#   de toute la fenêtre (aucun autre n'apparaît nulle part après lui, ce
#   qui garantit que `_find_latest_valid_leg` continue de l'utiliser).
# - idx11 : swing haut confirmé (140) — extrémité de la jambe, choisie
#   par `_find_latest_valid_leg` comme le premier swing haut après idx6.
# - idx16 : UN SECOND swing haut confirmé, plus bas (101), formé APRÈS
#   idx11 par une remontée isolée pendant le repli général (idx7-18
#   volontairement à ranges larges et chevauchants pour n'introduire
#   AUCUN gap accidentel de 3 bougies qui chevaucherait la zone de
#   Fibonacci ci-dessous). C'est LUI, pas idx11, qui devient le DERNIER
#   swing haut de la fenêtre — indispensable : `compute_structural_
#   regime` teste la clôture contre le DERNIER swing (101), pas contre
#   celui de la jambe (140) — une clôture DANS la zone de retracement de
#   la jambe [90,140] ne peut, par construction, jamais dépasser 140
#   (voir docstring de `compute_structural_regime`).
# - Zone de Fibonacci de la jambe [90,140] : [100.7, 109.1].
# - idx19-22 : plateau (bas identiques, ties) pour ne former AUCUN
#   nouveau swing bas qui remplacerait l'ancre idx6.
# - FVG haussier idx22->idx24 (zone [85,103]) chevauchant la zone
#   ci-dessus ; idx23 hors de portée de confirmation (effet de bord),
#   volontairement — voir docstring de find_confirmed_swings.
# - Clôture finale (idx24=105) : dans la zone Fibonacci ET > 101
#   (dernier swing haut) -> BOS confirmé -> régime "long".
_RECENT_LONG = [
    (100, 100, 100), (100, 100, 100), (100, 100, 100), (100, 100, 100),
    (100, 100, 100), (100, 100, 100),                                  # idx0-5 : bruit plat
    (97, 90, 93),      # idx6 : swing bas (ancre de la jambe)
    (120, 91, 100), (135, 95, 115), (138, 100, 125), (139, 105, 130),  # idx7-10 : montée, ranges larges (pas de gap)
    (140, 110, 135),   # idx11 : swing haut (140, extrémité de la jambe)
    (125, 101, 115), (110, 95, 100),                                   # idx12-13 : repli, ranges larges
    (97, 79, 90), (93, 75, 85),                                        # idx14-15 : repli, hauts < 101 (condition du bump)
    (101, 70, 90),     # idx16 : SECOND swing haut (101), dernier de la fenêtre
    (95, 65, 80), (90, 60, 75),                                        # idx17-18 : repli après le bump
    (85, 55, 70), (85, 55, 70), (85, 55, 70), (85, 55, 70),            # idx19-22 : plateau (ties, aucun nouveau swing bas)
    (100, 90, 94),     # idx23 : C2 du FVG, hors de portée de confirmation
    (109, 103, 105),   # idx24 : C3 du FVG, clôture courante (dans la zone ET > 101)
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
    # Depuis la bascule du 23/08/2026, la garde de longueur minimale ne
    # dépend plus de MA_PERIOD (200) mais de RECENT_WINDOW+2*FRACTAL_K+1
    # (25, voir _evaluate_entry) — 10 bougies est en dessous.
    candles = _build_candles(_RECENT_LONG[:10], baseline_n=0)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_flat_regime_returns_none():
    candles = _build_candles([(100, 100, 100)] * 25)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_no_valid_leg_returns_none():
    # Rampe monotone, aucun swing dans la fenêtre récente -> ni régime
    # structurel (compute_structural_regime exige des swings des deux
    # côtés) ni jambe possible.
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
    # low(idx24) <= high(idx22)=85, la même jambe/zone/régime restent
    # valides (validé en exécutant le code réel, voir docs/DECISIONS.md).
    recent[24] = (109, 85, 105)
    candles = _build_candles(recent)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_regime_confirmed_but_no_valid_leg_returns_none():
    # Régime structurel confirmé (cassure baissière réelle), mais AUCUNE
    # jambe possible dans ce sens : le swing bas précède chronologiquement
    # le swing haut (`_find_latest_valid_leg` exige l'inverse pour
    # "short" — un swing bas APRÈS le dernier swing haut). Couvre la
    # branche `leg is None` de `_evaluate_entry` distincte du cas "aucun
    # swing du tout" (régime déjà None avant même la recherche de jambe).
    recent = (
        [(100, 100, 100)] * 6
        + [(90, 80, 85)]                          # idx6 : swing bas (80)
        + [(95, 90, 93), (100, 95, 98)]            # idx7-8 : contexte
        + [(120, 110, 115)]                        # idx9 : swing haut (120)
        + [(100, 95, 98), (95, 90, 93)]            # idx10-11 : contexte
        + [(70, 60, 65)] * 13                      # idx12-24 : clôture finale 65 (< 80)
    )
    candles = _build_candles(recent, baseline_n=0)
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", None) is None


def test_recent_window_and_fractal_k_constants():
    assert RECENT_WINDOW == 20
    assert FRACTAL_K == 2
