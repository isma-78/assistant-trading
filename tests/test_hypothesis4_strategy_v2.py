"""
tests/test_hypothesis4_strategy_v2.py — H4/L4 (refonte 29/08/2026, voir
docs/HYPOTHESES.md). Les tests anti-lookahead de `find_confirmed_pivots`
sont écrits AVANT `src/hypothesis4_strategy_v2.py` (instruction explicite
du 29/08/2026) — ils doivent échouer sur toute implémentation naïve qui
lirait une bougie postérieure à celle évaluée.
"""

import pytest

from src.hypothesis4_strategy_v2 import (
    MAX_PIVOT_DISTANCE_BARS,
    PIVOT_FRACTAL_N,
    STOP_ATR_MULT,
    compute_obv,
    compute_rsi_series,
    evaluate_entry,
    find_confirmed_pivots,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close, volume=None):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close, volume=volume)


def _flat(n, price=100.0, volume=10.0):
    return [_c(i, price + 1, price - 1, price, volume) for i in range(n)]


# ---------------------------------------------------------------------------
# find_confirmed_pivots — ANTI-LOOKAHEAD (écrit avant l'implémentation)
# ---------------------------------------------------------------------------

def _series_with_low_pivot_at(index, total_len, base=100.0):
    """Série plate à `base`, sauf un creux net à `index` (bas local
    évident, fractal_n=3 : 3 bougies avant/après doivent être plus
    hautes)."""
    candles = list(_flat(total_len, price=base))
    candles[index] = _c(index, base + 1, base - 10, base - 10)
    return candles


def test_find_confirmed_pivots_not_reported_without_enough_trailing_bars():
    # Creux net à l'index 5, fractal_n=3, mais seulement 2 bougies APRÈS
    # (indices 6,7 -> longueur totale 8) : pas encore confirmé, ne doit
    # JAMAIS apparaître dans le résultat.
    candles = _series_with_low_pivot_at(5, total_len=8)
    pivots = find_confirmed_pivots(candles, fractal_n=3)
    assert all(idx != 5 for idx, _, _ in pivots)


def test_find_confirmed_pivots_reported_once_fully_confirmed():
    # Même creux, mais avec exactement 3 bougies après (longueur 9) : le
    # pivot devient confirmable.
    candles = _series_with_low_pivot_at(5, total_len=9)
    pivots = find_confirmed_pivots(candles, fractal_n=3)
    assert (5, "low", candles[5].low) in pivots


def test_find_confirmed_pivots_prefix_result_unaffected_by_future_candles():
    # Preuve directe anti-lookahead : le résultat pour une évaluation "à
    # l'instant t" (liste tronquée à t) est strictement identique, que
    # l'on tronque là ou qu'on garde des bougies futures au-delà — les
    # bougies futures ne peuvent QUE ajouter de nouveaux pivots (ceux
    # devenus confirmables entre-temps), jamais changer un résultat déjà
    # établi sur le préfixe.
    candles = _series_with_low_pivot_at(5, total_len=9)
    extended = candles + [_c(9, 101, 99, 100), _c(10, 101, 99, 100)]
    prefix_result = find_confirmed_pivots(candles, fractal_n=3)
    extended_result = find_confirmed_pivots(extended, fractal_n=3)
    assert set(prefix_result) <= set(extended_result)
    assert (5, "low", candles[5].low) in prefix_result
    assert (5, "low", candles[5].low) in extended_result


def test_find_confirmed_pivots_high_pivot_symmetric():
    candles = list(_flat(9, price=100.0))
    candles[4] = _c(4, 110.0, 99.0, 100.0)  # haut net à l'index 4
    pivots = find_confirmed_pivots(candles, fractal_n=3)
    assert (4, "high", 110.0) in pivots


def test_find_confirmed_pivots_insufficient_history_returns_empty():
    assert find_confirmed_pivots(_flat(5), fractal_n=3) == []


def test_find_confirmed_pivots_no_pivot_in_flat_series():
    assert find_confirmed_pivots(_flat(20), fractal_n=3) == []


# ---------------------------------------------------------------------------
# compute_rsi_series / compute_obv
# ---------------------------------------------------------------------------

def test_compute_rsi_series_none_before_enough_history():
    series = compute_rsi_series(_flat(10), period=14)
    assert all(v is None for v in series)


def test_compute_rsi_series_length_matches_candles():
    candles = _flat(20)
    series = compute_rsi_series(candles, period=14)
    assert len(series) == len(candles)
    assert series[14] is not None


def test_compute_obv_none_when_any_volume_missing():
    candles = _flat(5, volume=10.0)
    candles[2] = _c(2, 101, 99, 100, volume=None)
    assert compute_obv(candles) is None


def test_compute_obv_accumulates_on_close_direction():
    candles = [
        _c(0, 101, 99, 100.0, volume=10.0),
        _c(1, 102, 100, 101.0, volume=5.0),   # hausse -> +5
        _c(2, 102, 99, 99.0, volume=3.0),     # baisse -> -3
        _c(3, 100, 99, 99.0, volume=2.0),     # inchangé -> +0
    ]
    obv = compute_obv(candles)
    assert obv == [0.0, 5.0, 2.0, 2.0]


# ---------------------------------------------------------------------------
# evaluate_entry — divergence bout en bout
# ---------------------------------------------------------------------------

def _bullish_divergence_series(fractal_n=PIVOT_FRACTAL_N):
    """Deux creux : le second plus BAS en prix (nouvelle extrémité) mais
    RSI et OBV plus HAUTS (divergence haussière classique) -> long.
    Plateau initial de 20 bougies : RSI(14) exige au moins 15 bougies
    d'historique avant de produire une valeur, sans quoi le premier
    pivot n'aurait jamais de RSI défini (valeurs vérifiées
    numériquement, pas devinées à la main sur un indicateur lissé)."""
    n = fractal_n
    candles = []
    t = 0
    for _ in range(20):
        candles.append(_c(t, 101, 99, 100.0, volume=10.0)); t += 1
    i1 = len(candles)
    candles.append(_c(t, 96, 90.0, 92.0, volume=50.0)); t += 1  # premier creux, forte baisse -> RSI bas, OBV impacté
    for _ in range(n):
        candles.append(_c(t, 96, 94, 95.0, volume=10.0)); t += 1  # remontée -> RSI se redresse, OBV monte
    for _ in range(5):
        candles.append(_c(t, 97, 95, 96.0, volume=10.0)); t += 1  # distance suffisante avant le second pivot
    i2 = len(candles)
    candles.append(_c(t, 91, 85.0, 87.0, volume=5.0)); t += 1  # second creux, PLUS BAS en prix, chute plus douce
    for _ in range(n + 3):
        candles.append(_c(t, 89, 87, 88.0, volume=10.0)); t += 1  # bougies STRICTEMENT identiques : aucun nouveau pivot possible (inégalité stricte requise)
    return candles, i1, i2


def test_evaluate_entry_no_signal_before_second_pivot_confirmed():
    candles, i1, i2 = _bullish_divergence_series()
    # tronqué à une bougie avant la confirmation complète du second pivot
    truncated = candles[: i2 + PIVOT_FRACTAL_N]
    assert evaluate_entry("BTCUSD", truncated) is None


def test_evaluate_entry_signal_fires_exactly_on_confirmation_bar():
    candles, i1, i2 = _bullish_divergence_series()
    exact = candles[: i2 + PIVOT_FRACTAL_N + 1]
    signal = evaluate_entry("BTCUSD", exact)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == exact[-1].close
    assert signal.stop_price < candles[i2].low  # au-delà du pivot, jamais dessus
    # Structure de sortie standard du projet (§2.10), pas l'ancien TP fixe/aucun trailing
    r = signal.entry_price - signal.stop_price
    assert signal.tp1 == pytest.approx(signal.entry_price + r)
    assert signal.tp2 == pytest.approx(signal.entry_price + 2 * r)


def test_evaluate_entry_no_signal_one_bar_after_confirmation_window():
    # La confirmation est un ÉVÉNEMENT ponctuel (une seule bougie) —
    # passé ce point, plus aucun signal pour ce couple de pivots.
    candles, i1, i2 = _bullish_divergence_series()
    one_more = candles[: i2 + PIVOT_FRACTAL_N + 2]
    assert evaluate_entry("BTCUSD", one_more) is None


def test_evaluate_entry_respects_require_obv_confirmation_flag():
    # Garde-fou du point 7 : rejoue la même config avec use_obv désactivé
    # — jamais une variable de grille, un simple paramètre diagnostique.
    candles, i1, i2 = _bullish_divergence_series()
    exact = candles[: i2 + PIVOT_FRACTAL_N + 1]
    with_obv = evaluate_entry("BTCUSD", exact, require_obv_confirmation=True)
    without_obv = evaluate_entry("BTCUSD", exact, require_obv_confirmation=False)
    assert with_obv is not None
    assert without_obv is not None


def test_evaluate_entry_no_signal_on_flat_series():
    assert evaluate_entry("BTCUSD", _flat(60)) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("BTCUSD", []) is None
    assert evaluate_entry("BTCUSD", None) is None


def _bearish_divergence_series(fractal_n=PIVOT_FRACTAL_N):
    """Symétrique de _bullish_divergence_series : deux hauts, le second
    PLUS HAUT en prix mais RSI/OBV plus BAS (divergence baissière) -> short."""
    n = fractal_n
    candles = []
    t = 0
    for _ in range(20):
        candles.append(_c(t, 101, 99, 100.0, volume=10.0)); t += 1
    i1 = len(candles)
    candles.append(_c(t, 110.0, 96, 108.0, volume=50.0)); t += 1  # premier haut, forte hausse -> RSI haut, OBV monte fort
    for _ in range(n):
        candles.append(_c(t, 106, 104, 105.0, volume=10.0)); t += 1  # repli
    for _ in range(5):
        candles.append(_c(t, 105, 103, 104.0, volume=10.0)); t += 1
    i2 = len(candles)
    candles.append(_c(t, 115.0, 109, 113.0, volume=5.0)); t += 1  # second haut, PLUS HAUT en prix, hausse plus molle
    for _ in range(n + 3):
        candles.append(_c(t, 111, 109, 110.0, volume=10.0)); t += 1
    return candles, i1, i2


def test_evaluate_entry_bearish_divergence_short_signal():
    candles, i1, i2 = _bearish_divergence_series()
    exact = candles[: i2 + PIVOT_FRACTAL_N + 1]
    signal = evaluate_entry("BTCUSD", exact)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price > candles[i2].high
    r = signal.stop_price - signal.entry_price
    assert signal.tp1 == pytest.approx(signal.entry_price - r)
    assert signal.tp2 == pytest.approx(signal.entry_price - 2 * r)


def test_evaluate_entry_none_when_too_few_candles_for_pivot_window():
    n = PIVOT_FRACTAL_N
    assert evaluate_entry("BTCUSD", _flat(2 * n)) is None  # last_index = 2n-1 < 2n


def test_evaluate_entry_none_when_atr_history_insufficient():
    n = PIVOT_FRACTAL_N
    # Assez de bougies pour la fenêtre de pivot (>= 2n+1) mais pas pour
    # ATR(14) (exige au moins 15 bougies).
    candles = _flat(2 * n + 1)
    assert len(candles) < 15
    assert evaluate_entry("BTCUSD", candles) is None


def test_evaluate_entry_none_when_no_earlier_pivot_within_distance():
    # Un seul pivot bas confirmé, aucun premier pivot antérieur du même
    # type -> pas de divergence calculable.
    n = PIVOT_FRACTAL_N
    candles = list(_flat(30))
    idx = len(candles) - n - 1
    candles[idx] = _c(idx, 101, 90.0, 95.0)
    assert evaluate_entry("BTCUSD", candles) is None


def test_evaluate_entry_none_when_earlier_pivot_rsi_undefined():
    # Premier pivot confirmé (index 3, >= fractal_n) mais trop tôt dans
    # la série pour que RSI(14) y soit défini (exige index >= 14) —
    # divergence non calculable malgré deux pivots bien détectés.
    n = PIVOT_FRACTAL_N
    candles = []
    t = 0
    for _ in range(n):
        candles.append(_c(t, 101, 99, 100.0, volume=10.0)); t += 1
    candles.append(_c(t, 96, 90.0, 92.0, volume=50.0)); t += 1  # premier pivot, index = n
    for _ in range(n):
        candles.append(_c(t, 96, 94, 95.0, volume=10.0)); t += 1
    for _ in range(15):
        candles.append(_c(t, 97, 95, 96.0, volume=10.0)); t += 1
    candles.append(_c(t, 91, 85.0, 87.0, volume=5.0)); t += 1  # second pivot, RSI défini ici
    for _ in range(n):
        candles.append(_c(t, 89, 87, 88.0, volume=10.0)); t += 1
    assert evaluate_entry("BTCUSD", candles) is None


def test_evaluate_entry_none_when_obv_required_but_volume_missing():
    candles, i1, i2 = _bullish_divergence_series()
    exact = list(candles[: i2 + PIVOT_FRACTAL_N + 1])
    exact[i1] = _c(i1, candles[i1].high, candles[i1].low, candles[i1].close, volume=None)
    assert evaluate_entry("BTCUSD", exact, require_obv_confirmation=True) is None


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal():
    class _Broken:
        def __len__(self):
            return 100

        def __getitem__(self, item):
            raise RuntimeError("boom")

    assert evaluate_entry("BTCUSD", _Broken()) is None


def test_module_constants_within_preregistered_grid():
    assert PIVOT_FRACTAL_N in (2, 3, 4)
    assert MAX_PIVOT_DISTANCE_BARS in (20, 40, 60)
    assert STOP_ATR_MULT in (1.0, 1.5, 2.0)
