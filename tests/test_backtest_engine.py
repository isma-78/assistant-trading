from dataclasses import dataclass
from datetime import date
from typing import Optional

import pytest

from src.backtest_engine import (
    DEFAULT_LOOKBACK,
    FINANCING_BPS_PER_DAY,
    SLIPPAGE_SPREAD_MULTIPLIER,
    BacktestResult,
    HistoricalBar,
    _advance_confirming_pointer,
    _bar_hour,
    _parse_date,
    _trailing_window,
    bar_from_raw,
    entry_execution_price,
    exit_execution_price,
    financing_adjusted_exit_price,
    replay_hypothesis,
)
from src.market_data import Candle
from src.risk_engine import AssetSpec, RiskCaps, RiskEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(time_utc, o, h, l, c, spread=0.02):
    half = spread / 2
    return HistoricalBar(
        time_utc=time_utc,
        open_bid=o - half, open_ask=o + half,
        high_bid=h - half, high_ask=h + half,
        low_bid=l - half, low_ask=l + half,
        close_bid=c - half, close_ask=c + half,
    )


def _flat_bars(n, level=100.0, spread=0.02):
    return [_bar(f"2026-01-{min(1 + i // 24, 28):02d}T{(i % 24):02d}:00:00", level, level, level, level, spread) for i in range(n)]


def _rising_bars(n, start=1.0, step=0.05, spread=0.001):
    bars = []
    for i in range(n):
        v = start + i * step
        time_utc = f"2026-01-{min(1 + i // 24, 28):02d}T{(i % 24):02d}:00:00"
        bars.append(_bar(time_utc, v, v, v, v, spread))
    return bars


@dataclass(frozen=True)
class _FakeSignal:
    direction: str
    entry_price: float
    stop_price: float
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: float = 1.0


def _make_risk_engine(min_units=1.0, pip_value=1.0, envelope_initial=1000.0):
    caps = RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=envelope_initial)
    whitelist = {"TEST": AssetSpec(symbol="TEST", min_units=min_units, pip_value_per_unit=pip_value)}
    return RiskEngine(caps=caps, whitelist=whitelist), whitelist


def _trigger_on_call(n, signal):
    calls = {"n": 0}

    def entry_fn(asset, candles):
        calls["n"] += 1
        if calls["n"] == n:
            return signal
        return None

    return entry_fn


# ---------------------------------------------------------------------------
# bar_from_raw / HistoricalBar.to_candle
# ---------------------------------------------------------------------------

def test_bar_from_raw_parses_valid_point():
    raw = {
        "snapshotTimeUTC": "2026-01-01T00:00:00",
        "openPrice": {"bid": 1.0, "ask": 1.02},
        "highPrice": {"bid": 1.1, "ask": 1.12},
        "lowPrice": {"bid": 0.9, "ask": 0.92},
        "closePrice": {"bid": 1.05, "ask": 1.07},
    }
    bar = bar_from_raw(raw)
    assert bar is not None
    assert bar.time_utc == "2026-01-01T00:00:00"
    assert bar.open_bid == 1.0 and bar.open_ask == 1.02


def test_bar_from_raw_carries_last_traded_volume():
    raw = {
        "snapshotTimeUTC": "2026-01-01T00:00:00",
        "openPrice": {"bid": 1.0, "ask": 1.02},
        "highPrice": {"bid": 1.1, "ask": 1.12},
        "lowPrice": {"bid": 0.9, "ask": 0.92},
        "closePrice": {"bid": 1.05, "ask": 1.07},
        "lastTradedVolume": 4053,
    }
    bar = bar_from_raw(raw)
    assert bar.volume == 4053
    assert bar.to_candle().volume == 4053


def test_bar_from_raw_missing_volume_is_none():
    raw = {
        "snapshotTimeUTC": "2026-01-01T00:00:00",
        "openPrice": {"bid": 1.0, "ask": 1.02},
        "highPrice": {"bid": 1.1, "ask": 1.12},
        "lowPrice": {"bid": 0.9, "ask": 0.92},
        "closePrice": {"bid": 1.05, "ask": 1.07},
    }
    bar = bar_from_raw(raw)
    assert bar.volume is None
    assert bar.to_candle().volume is None


def test_bar_from_raw_missing_field_returns_none():
    assert bar_from_raw({"openPrice": {"bid": 1.0, "ask": 1.02}}) is None


def test_bar_from_raw_missing_bid_ask_returns_none():
    raw = {
        "snapshotTimeUTC": "t",
        "openPrice": {"bid": 1.0},  # ask manquant
        "highPrice": {"bid": 1.0, "ask": 1.0},
        "lowPrice": {"bid": 1.0, "ask": 1.0},
        "closePrice": {"bid": 1.0, "ask": 1.0},
    }
    assert bar_from_raw(raw) is None


def test_historical_bar_to_candle_uses_mid_price():
    bar = _bar("t", 100.0, 110.0, 90.0, 105.0, spread=2.0)
    candle = bar.to_candle()
    assert candle.open == pytest.approx(100.0)
    assert candle.high == pytest.approx(110.0)
    assert candle.low == pytest.approx(90.0)
    assert candle.close == pytest.approx(105.0)
    assert candle.time_utc == "t"


def test_historical_bar_spread_open():
    bar = _bar("t", 100.0, 100.0, 100.0, 100.0, spread=0.5)
    assert bar.spread_open == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Modèle de coûts
# ---------------------------------------------------------------------------

def test_entry_execution_price_long_pays_ask_no_slippage():
    # Recalibre le 26/08/2026 (voir docs/DECISIONS.md) : l'entree est un
    # ordre LIMITE (paye au pire son propre prix), jamais un ordre marche
    # - aucun slippage supplementaire, confirme sur 47 remplissages reels
    # (0 pire que le prix limite demande).
    bar = _bar("t", 100.0, 100.0, 100.0, 100.0, spread=0.2)
    price = entry_execution_price("long", bar)
    assert price == pytest.approx(bar.open_ask)


def test_entry_execution_price_short_receives_bid_no_slippage():
    bar = _bar("t", 100.0, 100.0, 100.0, 100.0, spread=0.2)
    price = entry_execution_price("short", bar)
    assert price == pytest.approx(bar.open_bid)


def test_entry_execution_price_unknown_direction_raises():
    bar = _bar("t", 100.0, 100.0, 100.0, 100.0)
    with pytest.raises(ValueError):
        entry_execution_price("sideways", bar)


def test_exit_execution_price_long_below_level():
    price = exit_execution_price("long", level_price=100.0, exit_bar_spread=0.2)
    assert price == pytest.approx(100.0 - 0.1 - 0.2 * SLIPPAGE_SPREAD_MULTIPLIER)
    assert price < 100.0


def test_exit_execution_price_short_above_level():
    price = exit_execution_price("short", level_price=100.0, exit_bar_spread=0.2)
    assert price == pytest.approx(100.0 + 0.1 + 0.2 * SLIPPAGE_SPREAD_MULTIPLIER)
    assert price > 100.0


def test_exit_execution_price_custom_slippage_multiplier():
    price_full = exit_execution_price("long", level_price=100.0, exit_bar_spread=0.2, slippage_multiplier=1.0)
    price_half = exit_execution_price("long", level_price=100.0, exit_bar_spread=0.2, slippage_multiplier=0.5)
    assert price_half > price_full  # slippage moindre -> moins defavorable pour une sortie long


def test_exit_execution_price_unknown_direction_raises():
    with pytest.raises(ValueError):
        exit_execution_price("sideways", 100.0, 0.1)


def test_financing_no_adjustment_same_day():
    d = date(2026, 1, 1)
    price = financing_adjusted_exit_price("long", 100.0, 100.0, d, d)
    assert price == pytest.approx(100.0)


def test_financing_no_adjustment_missing_dates():
    assert financing_adjusted_exit_price("long", 100.0, 100.0, None, date(2026, 1, 2)) == pytest.approx(100.0)
    assert financing_adjusted_exit_price("long", 100.0, 100.0, date(2026, 1, 1), None) == pytest.approx(100.0)


def test_financing_applies_cost_long_after_days_held():
    entry_date = date(2026, 1, 1)
    exit_date = date(2026, 1, 4)  # 3 jours civils
    price = financing_adjusted_exit_price("long", 100.0, 100.0, entry_date, exit_date)
    expected_cost = 100.0 * (FINANCING_BPS_PER_DAY / 10000.0) * 3
    assert price == pytest.approx(100.0 - expected_cost)


def test_financing_applies_cost_short_after_days_held():
    entry_date = date(2026, 1, 1)
    exit_date = date(2026, 1, 2)
    price = financing_adjusted_exit_price("short", 100.0, 100.0, entry_date, exit_date)
    expected_cost = 100.0 * (FINANCING_BPS_PER_DAY / 10000.0) * 1
    assert price == pytest.approx(100.0 + expected_cost)


def test_financing_unknown_direction_raises():
    with pytest.raises(ValueError):
        financing_adjusted_exit_price("sideways", 100.0, 100.0, date(2026, 1, 1), date(2026, 1, 2))


# ---------------------------------------------------------------------------
# replay_hypothesis — bout en bout
# ---------------------------------------------------------------------------

def test_replay_no_signal_ever_produces_no_trades():
    risk_engine, whitelist = _make_risk_engine()
    bars = _flat_bars(10)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=lambda a, c: None, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert result.trades == []
    assert result.final_envelope_balance == pytest.approx(1000.0)
    assert result.final_simulated_reserve == pytest.approx(0.0)


def test_replay_signal_on_last_bar_produces_no_trade():
    risk_engine, whitelist = _make_risk_engine()
    bars = _flat_bars(3)
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0)
    entry_fn = _trigger_on_call(3, signal)  # déclenche au dernier appel possible (t=2, dernière bougie)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert result.trades == []


def test_replay_stop_hit_full_lifecycle_negative_pnl():
    risk_engine, whitelist = _make_risk_engine()
    bars = [
        _bar("2026-01-01T00:00:00", 100, 100, 100, 100),  # t=0 : appel 1, pas de signal
        _bar("2026-01-02T00:00:00", 100, 100, 100, 100),  # t=1 : appel 2, signal déclenché ici
        _bar("2026-01-03T00:00:00", 100, 101, 60, 90),    # t=2 : exécution (open=100) PUIS stop touché ce même bougie (low=60)
        _bar("2026-01-04T00:00:00", 90, 90, 90, 90),
    ]
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0)
    entry_fn = _trigger_on_call(2, signal)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "long"
    assert trade.exit_reason == "close_full_stop"
    assert trade.r_multiple_total == pytest.approx(-1.0, abs=0.05)
    assert trade.pnl_eur < 0
    assert result.final_envelope_balance < 1000.0


def test_replay_slippage_multiplier_affects_pnl():
    # 24/08/2026 (voir docs/DECISIONS.md) : un multiplicateur plus faible
    # doit produire un R-multiple moins negatif (moins de cout) sur un
    # meme scenario perdant, toutes choses egales par ailleurs — spread
    # large (2.0) pour rendre l'effet mesurable sur un stop de 10 points.
    risk_engine, whitelist = _make_risk_engine()
    bars = [
        _bar("2026-01-01T00:00:00", 100, 100, 100, 100, spread=2.0),
        _bar("2026-01-02T00:00:00", 100, 100, 100, 100, spread=2.0),
        _bar("2026-01-03T00:00:00", 100, 101, 60, 90, spread=2.0),
        _bar("2026-01-04T00:00:00", 90, 90, 90, 90, spread=2.0),
    ]
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0)

    result_full = replay_hypothesis(
        "TEST", bars, entry_fn=_trigger_on_call(2, signal), risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5, slippage_multiplier=1.0,
    )
    result_half = replay_hypothesis(
        "TEST", bars, entry_fn=_trigger_on_call(2, signal), risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5, slippage_multiplier=0.5,
    )
    assert result_full.trades[0].r_multiple_total < result_half.trades[0].r_multiple_total
    assert result_full.final_envelope_balance < result_half.final_envelope_balance


def test_replay_tp1_tp2_then_final_stop_partials_sum_to_one():
    risk_engine, whitelist = _make_risk_engine()
    bars = [
        _bar("2026-01-01T00:00:00", 50, 50, 50, 50),      # t=0 : appel 1
        _bar("2026-01-02T00:00:00", 50, 50, 50, 50),      # t=1 : appel 2
        _bar("2026-01-03T00:00:00", 50, 50, 50, 50),      # t=2 : appel 3, signal déclenché ici
        _bar("2026-01-04T00:00:00", 100, 101, 99, 100),   # t=3 : exécution, ni stop ni TP1 touché
        _bar("2026-01-05T00:00:00", 101, 106, 100, 105),  # t=4 : TP1 (105) touché
        _bar("2026-01-06T00:00:00", 106, 111, 105, 110),  # t=5 : TP2 (110) touché
        _bar("2026-01-07T00:00:00", 105, 105, 50, 60),    # t=6 : chute -> stop (breakeven) touché, position soldée
    ]
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0, tp1=105.0, tp2=110.0)
    entry_fn = _trigger_on_call(3, signal)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.tp1 == pytest.approx(105.0)
    assert trade.tp2 == pytest.approx(110.0)
    assert trade.exit_reason == "close_full_stop"
    # TP1 (+1R) et TP2 (+2R) gagnants, reliquat clos au breakeven (~0R) :
    # R total pondéré doit rester nettement positif.
    assert trade.r_multiple_total > 0.5


def test_replay_only_one_position_at_a_time():
    risk_engine, whitelist = _make_risk_engine()
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0)
    # entry_fn retourne un signal a CHAQUE appel — si plus d'une position
    # pouvait s'ouvrir simultanément, on verrait plusieurs trades.
    bars = [
        _bar("2026-01-01T00:00:00", 100, 100, 100, 100),
        _bar("2026-01-02T00:00:00", 100, 101, 99, 100),
        _bar("2026-01-03T00:00:00", 100, 101, 99, 100),
        _bar("2026-01-04T00:00:00", 100, 101, 99, 100),
        _bar("2026-01-05T00:00:00", 100, 101, 40, 60),  # finit par toucher le stop
    ]
    result = replay_hypothesis(
        "TEST", bars, entry_fn=lambda a, c: signal, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert len(result.trades) == 1


def test_replay_decide_entry_rejection_no_trade_opened():
    # Confiance sous le seuil -> decide_entry rejette systematiquement,
    # aucun trade ne doit jamais s'ouvrir malgre un signal repete.
    risk_engine, whitelist = _make_risk_engine()
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0, confidence=0.0)
    bars = _flat_bars(6)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=lambda a, c: signal, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.5, lookback=5,
    )
    assert result.trades == []
    assert result.final_envelope_balance == pytest.approx(1000.0)


def test_replay_short_direction_stop_hit():
    risk_engine, whitelist = _make_risk_engine()
    bars = [
        _bar("2026-01-01T00:00:00", 100, 100, 100, 100),
        _bar("2026-01-02T00:00:00", 100, 100, 100, 100),  # signal declenche ici
        _bar("2026-01-03T00:00:00", 100, 140, 99, 130),   # execution (open=100) puis stop (110) touche (high=140)
    ]
    signal = _FakeSignal(direction="short", entry_price=100.0, stop_price=110.0)
    entry_fn = _trigger_on_call(2, signal)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "short"
    assert trade.pnl_eur < 0


def test_replay_take_profit_fixed_hypothesis4_style():
    risk_engine, whitelist = _make_risk_engine()
    bars = [
        _bar("2026-01-01T00:00:00", 100, 100, 100, 100),
        _bar("2026-01-02T00:00:00", 100, 100, 100, 100),  # signal
        _bar("2026-01-03T00:00:00", 100, 101, 99, 100),   # execution, rien touche
        _bar("2026-01-04T00:00:00", 100, 108, 99, 105),   # take_profit (105) touche
    ]
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0, take_profit=105.0)
    entry_fn = _trigger_on_call(2, signal)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "close_full_tp"
    assert trade.take_profit == pytest.approx(105.0)
    assert trade.pnl_eur > 0


def test_replay_donchian_trailing_flag_no_crash_stop_hit():
    # Style H1 (tp1=None) : is_donchian_trailing=True doit transmettre
    # `candles` a evaluate_position_management sans jamais planter, meme
    # avec un historique insuffisant pour recalculer le canal
    # (compute_trailing_stop_channel est fail-safe, voir trend_strategy.py).
    risk_engine, whitelist = _make_risk_engine()
    bars = [
        _bar("2026-01-01T00:00:00", 100, 100, 100, 100),
        _bar("2026-01-02T00:00:00", 100, 100, 100, 100),
        _bar("2026-01-03T00:00:00", 100, 101, 40, 60),  # execution puis stop touche
    ]
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0)  # tp1/tp2 None par defaut
    entry_fn = _trigger_on_call(2, signal)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5, is_donchian_trailing=True,
    )
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "close_full_stop"


# ---------------------------------------------------------------------------
# _advance_confirming_pointer / _trailing_window — correctif du 25/08/2026
# (alignement par horodatage, pas par position dans la liste : own_bars
# et les series de confirmation n'ont pas le meme nombre de bougies en
# pratique, voir docs/DECISIONS.md)
# ---------------------------------------------------------------------------

def _candle(time_utc, value=100.0):
    return Candle(time_utc=time_utc, open=value, high=value, low=value, close=value)


def test_advance_confirming_pointer_stops_before_future_bar():
    series = [_candle("2026-01-01T00:00:00"), _candle("2026-01-01T05:00:00"), _candle("2026-01-01T06:00:00")]
    # A l'instant 01h, seule la 1ere bougie (00h) est deja close - les
    # bougies a 05h/06h sont dans le futur relatif a cet instant, meme
    # si elles occupent les indices 1 et 2 (index-based les aurait incluses).
    ptr = _advance_confirming_pointer(series, 0, "2026-01-01T01:00:00")
    assert ptr == 1


def test_advance_confirming_pointer_includes_bar_at_exact_time():
    series = [_candle("2026-01-01T00:00:00"), _candle("2026-01-01T01:00:00")]
    ptr = _advance_confirming_pointer(series, 0, "2026-01-01T01:00:00")
    assert ptr == 2


def test_advance_confirming_pointer_never_goes_backward():
    series = [_candle("2026-01-01T00:00:00"), _candle("2026-01-01T01:00:00"), _candle("2026-01-01T02:00:00")]
    ptr = _advance_confirming_pointer(series, 2, "2026-01-01T00:30:00")  # instant anterieur au pointeur deja atteint
    assert ptr == 2  # ne recule jamais, meme si as_of_time est "avant"


def test_advance_confirming_pointer_empty_series():
    assert _advance_confirming_pointer([], 0, "2026-01-01T00:00:00") == 0


def test_trailing_window_respects_lookback():
    series = [_candle(f"2026-01-01T{i:02d}:00:00") for i in range(10)]
    window = _trailing_window(series, pointer=7, lookback=3)
    assert [c.time_utc for c in window] == ["2026-01-01T04:00:00", "2026-01-01T05:00:00", "2026-01-01T06:00:00"]


def test_trailing_window_pointer_less_than_lookback():
    series = [_candle(f"2026-01-01T{i:02d}:00:00") for i in range(3)]
    window = _trailing_window(series, pointer=2, lookback=10)
    assert len(window) == 2


def test_replay_regime_confirmation_handles_mismatched_confirming_length():
    # Reproduction directe du bug corrige le 25/08/2026 : own_bars a 230
    # bougies horaires, les series de confirmation en ont 460 (2x plus
    # fines, meme periode calendaire) - un alignement par index aurait
    # servi des bougies decalees dans le temps. Verifie juste l'absence
    # de crash et un comportement coherent (regime toujours resolu
    # "long" sur une serie de confirmation strictement montante, quelle
    # que soit sa longueur relative a own_bars).
    n = 230
    own_bars = _flat_bars(n, level=50.0)
    # Bougies de confirmation deux fois plus nombreuses (pas de gap de 24
    # bougies mais un pas de 30 minutes reel), memes bornes calendaires.
    us_rising_fine = []
    for i in range(n * 2):
        v = 1.0 + i * 0.025  # meme pente cumulee sur la periode que _rising_bars(n, step=0.05)
        day = min(1 + i // 48, 28)
        hour = (i // 2) % 24
        minute = 30 if i % 2 else 0
        us_rising_fine.append(_bar(f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:00", v, v, v, v, spread=0.001))
    confirming = {"US30": us_rising_fine, "US100": us_rising_fine}

    signal = _FakeSignal(direction="long", entry_price=50.0, stop_price=40.0)
    calls = {"n": 0}

    def entry_fn(asset, candles):
        calls["n"] += 1
        if calls["n"] == 225:
            return signal
        return None

    own_bars[226] = _bar(own_bars[226].time_utc, 50, 51, 30, 35)
    risk_engine, whitelist = _make_risk_engine()
    result = replay_hypothesis(
        "TEST", own_bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=DEFAULT_LOOKBACK,
        require_regime_confirmation=True, confirming_bars=confirming,
    )
    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"


def test_replay_regime_confirmation_blocks_mismatched_direction():
    n = 230
    own_bars = _flat_bars(n, level=50.0)
    us_rising = _rising_bars(n, start=1.0, step=0.05)  # regime "long" une fois assez d'historique
    confirming = {"US30": us_rising, "US100": us_rising}

    signal = _FakeSignal(direction="short", entry_price=100.0, stop_price=110.0)
    # Beaucoup d'appels avant que le regime ne soit confirme "long" (0h/8h/13h) —
    # on declenche systematiquement un signal "short", qui doit TOUJOURS
    # etre rejete (jamais concordant avec un regime confirme "long").
    risk_engine, whitelist = _make_risk_engine()
    result = replay_hypothesis(
        "TEST", own_bars, entry_fn=lambda a, c: signal, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=DEFAULT_LOOKBACK,
        require_regime_confirmation=True, confirming_bars=confirming,
    )
    assert result.trades == []


def test_replay_regime_confirmation_allows_matching_direction():
    n = 230
    own_bars = _flat_bars(n, level=50.0)
    # Bougie de cloture (apres l'execution a t=225) : plonge sous le stop
    # (40) pour que la position simulee se ferme dans l'historique
    # disponible — sinon elle resterait ouverte indefiniment (bougies
    # plates) et n'apparaitrait jamais dans result.trades.
    own_bars[226] = _bar(own_bars[226].time_utc, 50, 51, 30, 35)
    us_rising = _rising_bars(n, start=1.0, step=0.05)
    confirming = {"US30": us_rising, "US100": us_rising}

    # entry_price/stop_price alignes sur le niveau reel des bougies "own"
    # (level=50.0) : sinon la tolerance de peremption de decide_entry
    # rejetterait le signal avant meme d'atteindre le controle de regime.
    signal = _FakeSignal(direction="long", entry_price=50.0, stop_price=40.0)
    calls = {"n": 0}

    def entry_fn(asset, candles):
        calls["n"] += 1
        # Declenche tard (call 225 sur 230 bougies) pour garantir qu'au
        # moins un rafraichissement de regime a eu lieu avec une fenetre
        # >= 200 bougies (MA_PERIOD), donc un regime "long" resolu.
        if calls["n"] == 225:
            return signal
        return None

    risk_engine, whitelist = _make_risk_engine()
    result = replay_hypothesis(
        "TEST", own_bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=DEFAULT_LOOKBACK,
        require_regime_confirmation=True, confirming_bars=confirming,
    )
    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"


def test_replay_regime_confirmation_without_confirming_bars_blocks_everything():
    risk_engine, whitelist = _make_risk_engine()
    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0)
    bars = _flat_bars(6)
    result = replay_hypothesis(
        "TEST", bars, entry_fn=lambda a, c: signal, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=5,
        require_regime_confirmation=True, confirming_bars=None,
    )
    assert result.trades == []


def test_replay_trailing_stop_update_after_tp1_tp2():
    # Assez de bougies avant le declenchement pour que l'ATR(14) soit
    # calculable au moment de la gestion post-TP2 (fenetre glissante).
    bars = _flat_bars(20, level=100.0)
    bars.append(_bar("2026-02-01T00:00:00", 100, 100, 100, 100))  # signal
    bars.append(_bar("2026-02-02T00:00:00", 100, 101, 99, 100))   # execution, rien touche
    bars.append(_bar("2026-02-03T00:00:00", 101, 106, 100, 105))  # TP1
    bars.append(_bar("2026-02-04T00:00:00", 106, 111, 105, 110))  # TP2
    bars.append(_bar("2026-02-05T00:00:00", 111, 120, 115, 119))  # pousse encore -> trailing suit
    bars.append(_bar("2026-02-06T00:00:00", 119, 119, 90, 95))    # repli -> trailing touche, cloture

    signal = _FakeSignal(direction="long", entry_price=100.0, stop_price=90.0, tp1=105.0, tp2=110.0)
    entry_fn = _trigger_on_call(21, signal)
    risk_engine, whitelist = _make_risk_engine()
    result = replay_hypothesis(
        "TEST", bars, entry_fn=entry_fn, risk_engine=risk_engine, whitelist=whitelist,
        envelope_initial=1000.0, confidence_threshold=0.0, lookback=DEFAULT_LOOKBACK,
    )
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "close_full_stop"


def test_parse_date_malformed_returns_none():
    assert _parse_date("not-a-date") is None
    assert _parse_date("") is None


def test_bar_hour_malformed_returns_none():
    assert _bar_hour("not-a-time") is None
    assert _bar_hour("") is None


def test_backtest_result_is_frozen_dataclass_with_expected_fields():
    result = BacktestResult(trades=[], final_envelope_balance=1000.0, final_simulated_reserve=0.0)
    assert result.trades == []
    assert result.final_envelope_balance == 1000.0
    assert result.final_simulated_reserve == 0.0
