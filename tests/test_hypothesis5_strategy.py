from dataclasses import dataclass
from unittest.mock import patch

import pytest

from src.hypothesis5_strategy import (
    RSI_PERIOD,
    RSI_THRESHOLD,
    TP1_R_MULTIPLE,
    TP2_R_MULTIPLE,
    Hypothesis5Signal,
    _compute_tp_levels,
    _rsi_just_crossed_threshold,
    compute_rsi,
    evaluate_entry,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _c(i, high, low, close):
    return Candle(time_utc=str(i), open=close, high=high, low=low, close=close)


def _closes(values):
    return [_c(i, v + 1, v - 1, v) for i, v in enumerate(values)]


# ---------------------------------------------------------------------------
# compute_rsi
# ---------------------------------------------------------------------------

def test_compute_rsi_insufficient_history_returns_none():
    assert compute_rsi(_closes([100 + i for i in range(10)]), RSI_PERIOD) is None


def test_compute_rsi_all_gains_is_100():
    # avg_loss = 0 -> branche dédiée (division par avg_loss impossible),
    # RSI plafonné à 100.
    assert compute_rsi(_closes([100 + i for i in range(16)]), RSI_PERIOD) == pytest.approx(100.0)


def test_compute_rsi_known_declining_then_rising_value():
    # 15 baisses de 1 (100->86) puis un gain net de +20 (86->106) :
    # seed avg_gain=0/avg_loss=1 sur les 14 premiers deltas, un seul pas
    # de lissage restant -> valeur calculable indépendamment.
    closes = list(range(100, 85, -1)) + [106]
    assert compute_rsi(_closes(closes), RSI_PERIOD) == pytest.approx(60.6060606060606)


# ---------------------------------------------------------------------------
# _rsi_just_crossed_threshold
# ---------------------------------------------------------------------------

# Fixture minimale et découplée de toute contrainte ICT (16 bougies) :
# déclin régulier (100->86) puis un fort rebond (106) sur la dernière
# bougie -> RSI passe de 0 à ~60.6, franchissement net du seuil 50.
_RSI_CROSS_UP_CLOSES = list(range(100, 85, -1)) + [106]
_RSI_CROSS_DOWN_CLOSES = [200 - v for v in _RSI_CROSS_UP_CLOSES]  # miroir exact


def test_rsi_just_crossed_threshold_long_true():
    assert _rsi_just_crossed_threshold(_closes(_RSI_CROSS_UP_CLOSES), "long") is True


def test_rsi_just_crossed_threshold_short_true():
    assert _rsi_just_crossed_threshold(_closes(_RSI_CROSS_DOWN_CLOSES), "short") is True


def test_rsi_just_crossed_threshold_no_cross_false():
    # RSI déjà et toujours au-dessus de 50 (hausse continue) : pas de
    # franchissement récent, condition volontairement stricte.
    closes = [100 + i * 2 for i in range(16)]
    assert _rsi_just_crossed_threshold(_closes(closes), "long") is False


def test_rsi_just_crossed_threshold_wrong_direction_no_cross_false():
    # Le RSI franchit bien 50 à la hausse, mais on teste "short" -> faux.
    assert _rsi_just_crossed_threshold(_closes(_RSI_CROSS_UP_CLOSES), "short") is False


def test_rsi_just_crossed_threshold_insufficient_history_rsi_now_none():
    assert _rsi_just_crossed_threshold(_closes([100 + i for i in range(10)]), "long") is False


def test_rsi_just_crossed_threshold_insufficient_history_rsi_prev_none():
    # Exactement 15 bougies : rsi_now calculable (>= period+1), mais
    # candles[:-1] n'a que 14 bougies -> rsi_prev = None.
    assert _rsi_just_crossed_threshold(_closes([100 + i for i in range(15)]), "long") is False


def test_rsi_just_crossed_threshold_unknown_direction_raises():
    with pytest.raises(ValueError):
        _rsi_just_crossed_threshold(_closes(_RSI_CROSS_UP_CLOSES), "sideways")


def test_rsi_period_and_threshold_constants():
    assert RSI_PERIOD == 14
    assert RSI_THRESHOLD == 50.0


# ---------------------------------------------------------------------------
# _compute_tp_levels
# ---------------------------------------------------------------------------

def test_compute_tp_levels_long():
    tp1, tp2 = _compute_tp_levels("long", entry_price=105.0, stop_price=90.0)
    assert tp1 == pytest.approx(105.0 + 1.0 * 15.0)
    assert tp2 == pytest.approx(105.0 + 2.0 * 15.0)


def test_compute_tp_levels_short():
    tp1, tp2 = _compute_tp_levels("short", entry_price=95.0, stop_price=110.0)
    assert tp1 == pytest.approx(95.0 - 1.0 * 15.0)
    assert tp2 == pytest.approx(95.0 - 2.0 * 15.0)


def test_compute_tp_levels_unknown_direction_raises():
    with pytest.raises(ValueError):
        _compute_tp_levels("sideways", entry_price=100.0, stop_price=90.0)


def test_r_multiples_are_1_and_2():
    assert TP1_R_MULTIPLE == 1.0
    assert TP2_R_MULTIPLE == 2.0


# ---------------------------------------------------------------------------
# evaluate_entry / _evaluate_entry — bout en bout
# ---------------------------------------------------------------------------
#
# V3 (24/08/2026, voir docs/DECISIONS.md/docs/HYPOTHESES.md) : la
# confluence ICT (Fibonacci/FVG) est retirée, seul le régime structurel +
# jambe d'impulsion (`ict_strategy.compute_structural_entry`) est requis
# EN PLUS du franchissement RSI.
#
# Les deux conditions (régime structurel + franchissement RSI) sont
# testées ENSEMBLE au moyen d'un double (`unittest.mock.patch`) sur
# `_compute_structural_entry` pour les cas positifs (long/short) :
# construire organiquement une seule fenêtre de bougies satisfaisant
# SIMULTANÉMENT la géométrie structurelle (régime + jambe, déjà validée
# dans tests/test_ict_strategy.py) ET une trajectoire RSI précise est
# sur-contraint. Découpler les deux dépendances isole correctement CE
# module (qui ne fait que les combiner) de la correction de
# `ict_strategy.compute_structural_entry` elle-même (déjà testée à 100%
# séparément). Le cas "régime structurel présent MAIS RSI non franchi"
# ci-dessous, lui, utilise une fenêtre RÉELLE (aucun double) pour prouver
# que le filtre RSI s'applique bien sur une vraie sortie de
# `ict_strategy.compute_structural_entry`.

def test_evaluate_entry_no_structural_signal_returns_none():
    with patch("src.hypothesis5_strategy._compute_structural_entry", return_value=None):
        assert evaluate_entry("EURUSD", _closes(_RSI_CROSS_UP_CLOSES)) is None


def test_evaluate_entry_structural_signal_present_but_no_rsi_cross_returns_none():
    # Fenêtre réelle (aucun double) : régime structurel + jambe valides
    # (même géométrie haussière que tests/test_ict_strategy.py, entrée=105,
    # stop=90 — la zone Fibonacci/FVG n'est même plus requise depuis la
    # V3, mais cette fenêtre les satisfait aussi, hérité du fixture
    # original), mais les clôtures intermédiaires (seules libres — hauts/
    # bas figés par la géométrie) ne produisent PAS de franchissement
    # RSI récent du seuil 50 (validé en exécutant le code réel : RSI
    # passe de ~55.7 à ~62.7, déjà au-dessus de 50 avant la clôture
    # courante -> aucun franchissement).
    recent = [
        (100, 100, 100), (100, 100, 100), (100, 100, 100), (100, 100, 100),
        (100, 100, 100), (100, 100, 100),
        (97, 90, 93),
        (120, 91, 92), (135, 95, 96), (138, 100, 101), (139, 105, 106),
        (140, 110, 111),
        (125, 101, 102), (110, 95, 96),
        (97, 79, 80), (93, 75, 76),
        (101, 70, 71),
        (95, 65, 66), (90, 60, 61),
        (85, 55, 56), (85, 55, 56), (85, 55, 56), (85, 55, 56),
        (100, 90, 91),
        (109, 103, 105),
    ]
    candles = [_c(i, h, l, cl) for i, (h, l, cl) in enumerate(recent)]
    assert evaluate_entry("EURUSD", candles) is None


def test_evaluate_entry_both_conditions_met_long():
    structural_signal = TrendSignal(asset="EURUSD", direction="long", entry_price=105.0, stop_price=90.0, confidence=1.0)
    with patch("src.hypothesis5_strategy._compute_structural_entry", return_value=structural_signal):
        signal = evaluate_entry("EURUSD", _closes(_RSI_CROSS_UP_CLOSES))
    assert signal is not None
    assert isinstance(signal, Hypothesis5Signal)
    assert signal.asset == "EURUSD"
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(105.0)
    assert signal.stop_price == pytest.approx(90.0)
    r = 15.0
    assert signal.tp1 == pytest.approx(105.0 + r)
    assert signal.tp2 == pytest.approx(105.0 + 2 * r)
    assert signal.confidence == 1.0


def test_evaluate_entry_both_conditions_met_short():
    structural_signal = TrendSignal(asset="EURUSD", direction="short", entry_price=95.0, stop_price=110.0, confidence=1.0)
    with patch("src.hypothesis5_strategy._compute_structural_entry", return_value=structural_signal):
        signal = evaluate_entry("EURUSD", _closes(_RSI_CROSS_DOWN_CLOSES))
    assert signal is not None
    assert signal.direction == "short"
    r = 15.0
    assert signal.tp1 == pytest.approx(95.0 - r)
    assert signal.tp2 == pytest.approx(95.0 - 2 * r)


def test_evaluate_entry_structural_signal_present_but_rsi_direction_mismatch_returns_none():
    # Régime structurel haussier disponible, mais le RSI franchit 50 à la
    # HAUSSE alors qu'on teste ici un signal structurel "short" (fenêtre
    # RSI construite pour un franchissement long, jamais short) -> aucune
    # des deux conditions n'est réunie dans le même sens, pas de signal.
    structural_signal = TrendSignal(asset="EURUSD", direction="short", entry_price=95.0, stop_price=110.0, confidence=1.0)
    with patch("src.hypothesis5_strategy._compute_structural_entry", return_value=structural_signal):
        signal = evaluate_entry("EURUSD", _closes(_RSI_CROSS_UP_CLOSES))
    assert signal is None


def test_evaluate_entry_never_raises_on_malformed_input():
    assert evaluate_entry("EURUSD", None) is None


def test_evaluate_entry_internal_error_is_fail_safe():
    @dataclass(frozen=True)
    class _BadDirectionSignal:
        asset: str
        direction: str
        entry_price: float
        stop_price: float
        confidence: float = 1.0

    bad_signal = _BadDirectionSignal(asset="EURUSD", direction="sideways", entry_price=100.0, stop_price=90.0)
    with patch("src.hypothesis5_strategy._compute_structural_entry", return_value=bad_signal):
        assert evaluate_entry("EURUSD", _closes(_RSI_CROSS_UP_CLOSES)) is None


def test_evaluate_entry_no_longer_requires_fibonacci_fvg_confluence():
    # V3 (24/08/2026) : contrairement à la version précédente (qui
    # déléguait à ict_strategy.evaluate_entry, confluence Fibonacci/FVG
    # incluse), un régime structurel valide SANS confluence ICT suffit
    # désormais, tant que le RSI franchit 50 dans le même sens. Double sur
    # `_compute_structural_entry` avec une clôture (145) délibérément hors
    # de toute zone de Fibonacci plausible pour la jambe (90-140) — la
    # confluence ICT complète (ict_strategy.evaluate_entry) rejetterait ce
    # cas (voir test_ict_strategy.test_evaluate_entry_price_outside_zone_
    # returns_none), mais compute_structural_entry (donc H5 V3) ne la
    # vérifie jamais.
    structural_signal = TrendSignal(asset="EURUSD", direction="long", entry_price=145.0, stop_price=90.0, confidence=1.0)
    with patch("src.hypothesis5_strategy._compute_structural_entry", return_value=structural_signal):
        signal = evaluate_entry("EURUSD", _closes(_RSI_CROSS_UP_CLOSES))
    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(145.0)
    assert signal.stop_price == pytest.approx(90.0)
