"""
tests/test_hypothesis2_strategy_v2.py — H2/L2 (refonte 29/08/2026, voir
docs/HYPOTHESES.md). Le test anti-lookahead d'Ichimoku est écrit AVANT
l'implémentation (instruction explicite du 29/08/2026) : le nuage
utilisable à la bougie t est calculé à partir de t-26 (senkou span A/B
décalés de 26 périodes vers l'avant par construction) — le test doit
échouer si le code lit une bougie postérieure à t-26 pour ce calcul.
"""

import pytest

from src.hypothesis2_strategy_v2 import (
    ATR_PERIOD,
    EMA_PERIOD,
    ICHIMOKU_KIJUN_PERIOD,
    ICHIMOKU_SENKOU_B_PERIOD,
    ICHIMOKU_SHIFT,
    ICHIMOKU_TENKAN_PERIOD,
    N_TF,
    RSI_PERIOD,
    RSI_THRESHOLD,
    SCORE_THRESHOLD,
    STRUCTURE_LOOKBACK,
    _compute_stop,
    compute_ema,
    compute_ichimoku_cloud_at,
    compute_tf_vote,
    evaluate_entry,
)
from src.hypothesis4_strategy_v2 import compute_rsi_series
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close)


def _trending_series(n, start=100.0, step=1.0):
    candles = []
    price = start
    for i in range(n):
        price += step
        candles.append(_c(i, price + 1, price - 1, price))
    return candles


# ---------------------------------------------------------------------------
# compute_ichimoku_cloud_at — ANTI-LOOKAHEAD (écrit avant l'implémentation)
# ---------------------------------------------------------------------------

def test_ichimoku_cloud_none_before_shift_plus_senkou_b_history():
    # Index 26 (= ICHIMOKU_SHIFT) exige des données jusqu'à l'index 0 —
    # mais senkou B (52 périodes) exige encore 52 bougies avant l'index
    # de base -> None tant que l'historique total est insuffisant.
    candles = _trending_series(30)
    assert compute_ichimoku_cloud_at(candles, index=26) is None


def test_ichimoku_cloud_defined_once_enough_history():
    candles = _trending_series(80)
    assert compute_ichimoku_cloud_at(candles, index=79) is not None


def test_ichimoku_cloud_result_unaffected_by_candles_after_shifted_base_index():
    # PREUVE ANTI-LOOKAHEAD DIRECTE : deux séries identiques jusqu'à
    # l'index (index - ICHIMOKU_SHIFT) puis DIFFÉRENTES au-delà — le
    # nuage utilisable À L'INDEX `index` ne doit dépendre QUE du préfixe
    # commun (jusqu'à index - 26), jamais de ce qui vient après.
    index = 79
    base_idx = index - ICHIMOKU_SHIFT
    common_prefix = _trending_series(base_idx + 1, start=100.0, step=1.0)

    series_a = list(common_prefix) + _trending_series(index - base_idx, start=200.0, step=1.0)
    series_b = list(common_prefix) + _trending_series(index - base_idx, start=200.0, step=-3.0)  # divergent après le pivot
    # même longueur, même préfixe, suffixes complètement différents
    assert len(series_a) == index + 1 == len(series_b)

    cloud_a = compute_ichimoku_cloud_at(series_a, index=index)
    cloud_b = compute_ichimoku_cloud_at(series_b, index=index)
    assert cloud_a is not None and cloud_b is not None
    assert cloud_a == cloud_b  # identique malgré des suffixes opposés


def test_ichimoku_cloud_naive_no_shift_implementation_would_diverge():
    # Contrôle négatif : si on omettait le décalage de 26 périodes (bug
    # de lookahead classique), le nuage "vu" à `index` dépendrait des
    # bougies récentes — précisément ce que le test précédent interdit.
    # Ici on vérifie que le nuage SANS décalage (base_idx = index) DIFFÈRE
    # bien entre les deux séries divergentes, confirmant que le test
    # ci-dessus est un test discriminant, pas un test qui passerait de
    # toute façon.
    index = 79
    base_idx = index - ICHIMOKU_SHIFT
    common_prefix = _trending_series(base_idx + 1, start=100.0, step=1.0)
    series_a = list(common_prefix) + _trending_series(index - base_idx, start=200.0, step=1.0)
    series_b = list(common_prefix) + _trending_series(index - base_idx, start=200.0, step=-3.0)

    cloud_a_naive = compute_ichimoku_cloud_at(series_a, index=index, shift=0)
    cloud_b_naive = compute_ichimoku_cloud_at(series_b, index=index, shift=0)
    assert cloud_a_naive != cloud_b_naive


# ---------------------------------------------------------------------------
# compute_ema
# ---------------------------------------------------------------------------

def test_compute_ema_insufficient_history_returns_none():
    assert compute_ema(_trending_series(5), period=EMA_PERIOD) is None


def test_compute_ema_matches_known_simple_case():
    # EMA d'une série constante = la constante elle-même.
    candles = [_c(i, 101, 99, 100.0) for i in range(EMA_PERIOD + 5)]
    assert compute_ema(candles, period=EMA_PERIOD) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# compute_tf_vote
# ---------------------------------------------------------------------------

def test_compute_tf_vote_long_when_all_three_indicators_agree():
    candles = _trending_series(120, start=100.0, step=1.0)  # tendance haussière franche
    vote = compute_tf_vote(candles, len(candles) - 1)
    assert vote == "long"


def test_compute_tf_vote_short_when_all_three_indicators_agree():
    candles = _trending_series(120, start=300.0, step=-1.0)  # tendance baissière franche
    vote = compute_tf_vote(candles, len(candles) - 1)
    assert vote == "short"


def test_compute_tf_vote_none_when_insufficient_history():
    assert compute_tf_vote(_trending_series(10), 9) is None


def test_compute_tf_vote_none_when_indicators_disagree():
    # Série plate : RSI proche de 50 (ambigu), EMA=prix (ambigu) -> pas
    # d'alignement franc.
    candles = [_c(i, 101, 99, 100.0) for i in range(120)]
    assert compute_tf_vote(candles, len(candles) - 1) is None


# ---------------------------------------------------------------------------
# evaluate_entry — bout en bout, multi-TF
# ---------------------------------------------------------------------------

def _long_ready_series(n=120):
    return _trending_series(n, start=100.0, step=1.0)


def test_evaluate_entry_long_when_all_tf_confirm():
    m15 = _long_ready_series()
    h1 = _long_ready_series()
    h4 = _long_ready_series()
    signal = evaluate_entry("EURUSD", m15, h1, h4)
    assert signal is not None
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"
    assert signal.entry_price == m15[-1].close


def test_evaluate_entry_none_when_fewer_than_n_tf_confirm():
    m15 = _long_ready_series()
    flat = [_c(i, 101, 99, 100.0) for i in range(120)]
    # Seul M15 confirme si N_TF > 1 -> pas de confluence suffisante.
    if N_TF > 1:
        assert evaluate_entry("EURUSD", m15, flat, flat) is None


def test_evaluate_entry_none_when_any_resolution_missing():
    m15 = _long_ready_series()
    assert evaluate_entry("EURUSD", m15, None, m15) is None
    assert evaluate_entry("EURUSD", m15, m15, None) is None


def test_evaluate_entry_none_on_flat_series():
    flat = [_c(i, 101, 99, 100.0) for i in range(120)]
    assert evaluate_entry("EURUSD", flat, flat, flat) is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", [], [], []) is None
    assert evaluate_entry("EURUSD", None, None, None) is None


def test_evaluate_entry_stop_uses_structure_or_atr_fallback():
    m15 = _long_ready_series()
    signal = evaluate_entry("EURUSD", m15, m15, m15)
    assert signal is not None
    assert signal.stop_price < signal.entry_price  # long : stop en dessous


def test_ichimoku_cloud_none_when_senkou_b_insufficient_but_tenkan_kijun_ok():
    # base_idx=30 : tenkan(9)/kijun(26) satisfaits, senkou_b(52) non.
    candles = _trending_series(30 + ICHIMOKU_SHIFT + 1)
    assert compute_ichimoku_cloud_at(candles, index=30 + ICHIMOKU_SHIFT) is None


def test_compute_tf_vote_out_of_range_index_returns_none():
    candles = _trending_series(20)
    assert compute_tf_vote(candles, -1) is None
    assert compute_tf_vote(candles, len(candles)) is None


def test_evaluate_entry_short_when_all_tf_confirm():
    m15 = _trending_series(120, start=300.0, step=-1.0)
    h1 = _trending_series(120, start=300.0, step=-1.0)
    h4 = _trending_series(120, start=300.0, step=-1.0)
    signal = evaluate_entry("EURUSD", m15, h1, h4)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price > signal.entry_price


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal():
    class _Broken:
        def __bool__(self):
            return True

        def __len__(self):
            return 100

        def __getitem__(self, item):
            raise RuntimeError("boom")

    assert evaluate_entry("EURUSD", _Broken(), _Broken(), _Broken()) is None


def test_compute_stop_structure_available_uses_donchian_channel():
    candles = _trending_series(STRUCTURE_LOOKBACK + 5)
    stop = _compute_stop(candles, "long", candles[-1].close)
    assert stop is not None


def test_compute_stop_falls_back_to_atr_when_structure_unavailable():
    # STRUCTURE_LOOKBACK(20) exige 21 bougies, ATR_PERIOD(14) seulement
    # 15 : avec 16 bougies, la structure est indisponible mais l'ATR non.
    assert STRUCTURE_LOOKBACK > ATR_PERIOD
    candles = _trending_series(ATR_PERIOD + 2)
    assert len(candles) < STRUCTURE_LOOKBACK + 1
    entry_price = candles[-1].close
    stop_long = _compute_stop(candles, "long", entry_price)
    stop_short = _compute_stop(candles, "short", entry_price)
    assert stop_long is not None and stop_long < entry_price
    assert stop_short is not None and stop_short > entry_price


def test_evaluate_entry_none_when_confluence_reached_but_m15_stop_unavailable():
    # H1+H4 confirment seuls (N_TF=2 atteint sans le vote M15) alors que
    # la série M15 elle-même est trop courte pour fournir ni structure
    # ni repli ATR pour le stop -> pas de signal, jamais un stop devine.
    short_m15 = _trending_series(3)
    h1 = _long_ready_series()
    h4 = _long_ready_series()
    assert evaluate_entry("EURUSD", short_m15, h1, h4) is None


def test_compute_stop_none_when_neither_structure_nor_atr_available():
    candles = _trending_series(3)
    assert _compute_stop(candles, "long", candles[-1].close) is None


def test_module_constants_within_preregistered_grid():
    assert EMA_PERIOD in (20, 50, 100)
    assert RSI_THRESHOLD in (50, 55)
    assert N_TF in (2, 3)
    assert SCORE_THRESHOLD in (2 / 3, 1.0)
    assert RSI_PERIOD == 14


def test_min_lookback_for_grid_covers_widest_grid_value():
    from src.hypothesis2_strategy_v2 import ICHIMOKU_SENKOU_B_PERIOD, ICHIMOKU_SHIFT, MIN_LOOKBACK_FOR_GRID
    widest_ema_period = 100  # borne haute de la grille pré-enregistrée {20,50,100}
    assert MIN_LOOKBACK_FOR_GRID >= max(widest_ema_period, ICHIMOKU_SHIFT + ICHIMOKU_SENKOU_B_PERIOD)
    assert (ICHIMOKU_TENKAN_PERIOD, ICHIMOKU_KIJUN_PERIOD, ICHIMOKU_SENKOU_B_PERIOD) == (9, 26, 52)
