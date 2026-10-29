import random

import pytest

from src import adequation_decision as ad
from src.adequation_decision import Trade


def _trades(rs, start="2024-04-01", step_days=1):
    from datetime import datetime, timedelta
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [Trade((d0 + timedelta(days=i * step_days)).strftime("%Y-%m-%dT10:00:00"),
                  (d0 + timedelta(days=i * step_days)).strftime("%Y-%m-%dT12:00:00"), r) for i, r in enumerate(rs)]


def _norm(n, mean, sd=1.0, seed=1):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_max_drawdown_and_top3():
    assert ad.max_drawdown([1, -2, -1, 3, -4]) == 4  # pic 1 -> creux -2 puis pic 1 -> -3
    assert ad.max_drawdown([]) == 0.0
    assert ad.top3_share([5, 3, 2, 1, -9]) == pytest.approx(10 / 11)
    assert ad.top3_share([-1, -2]) is None


def test_period_stats_small_degenerate_and_regular():
    assert ad.period_stats([]).mean is None
    assert ad.period_stats([1.0]).p_lower is None
    flat = ad.period_stats([-1.0, -1.0, -1.0])
    assert flat.p_lower == 0.0 and flat.p_upper == 1.0 and flat.t_stat is None
    up = ad.period_stats([1.0, 1.0])
    assert up.p_upper == 0.0
    st = ad.period_stats([0.5, -0.2, 0.4, 0.1, 0.3])
    assert st.p_upper == pytest.approx(1 - st.p_lower) and st.t_stat > 0


def test_split_trades_by_entry_date_and_windows():
    trades = [
        Trade("2018-12-31T00:00:00", "2019-01-01T00:00:00", 9.0),   # avant 2019 : ignoré
        Trade("2019-06-01T00:00:00", "2019-06-02T00:00:00", 1.0),   # train, W1
        Trade("2023-12-31T23:00:00", "2024-01-02T00:00:00", 2.0),   # train, W3
        Trade("2024-03-20T00:00:00", "2024-03-21T00:00:00", 3.0),   # test (borne incluse), W3
        Trade("2025-02-01T00:00:00", "2025-02-02T00:00:00", 4.0),   # test, W4
    ]
    parts = ad.split_trades(trades)
    assert parts["train"] == [1.0, 2.0] and parts["test"] == [3.0, 4.0]
    assert parts["W1"] == [1.0] and parts["W3"] == [2.0, 3.0] and parts["W4"] == [4.0] and parts["W2"] == []


def test_holm_adjust_monotone_and_capped():
    adj = ad.holm_adjust({"a": 0.001, "b": 0.02, "c": 0.5})
    assert adj["a"] == pytest.approx(0.003) and adj["b"] == pytest.approx(0.04) and adj["c"] == 0.5
    adj2 = ad.holm_adjust({"a": 0.02, "b": 0.011, "c": 0.9})
    assert adj2["b"] == pytest.approx(0.033) and adj2["a"] >= adj2["b"] and adj2["c"] == 0.9
    assert ad.holm_adjust({"x": 0.9})["x"] == 0.9


def _pair(h, a, rs_train, rs_test, windows_positive=True):
    trades = _trades(rs_train, start="2019-02-01", step_days=1) + _trades(rs_test, start="2024-04-01")
    return ad.evaluate_pair(h, a, trades)


def test_classify_below_min_trades_is_inconclusive():
    r = _pair("H1", "GBPUSD", [], [-1.0] * 29)
    ad.classify([r])
    assert r.verdict == ad.INCONCLUSIVE and "n_test=29" in r.reasons[0]


def test_classify_removes_significantly_negative_pair():
    r = _pair("H1", "BTCUSD", [], _norm(60, -0.6))
    ad.classify([r])
    assert r.verdict == ad.REMOVE and r.p_neg_holm <= 0.05


def _robust_trades(mean, sd=0.5, per_window=40, seed=3):
    """40 trades par fenêtre W1..W4 (test = 2024-03-20+ : fin de W3 + W4) avec top-3 non dominant."""
    rng = random.Random(seed)
    out = []
    from datetime import datetime, timedelta
    for start in ("2019-03-01", "2021-03-01", "2023-03-01", "2025-03-01"):
        d0 = datetime.strptime(start, "%Y-%m-%d")
        for i in range(per_window):
            day = d0 + timedelta(days=i * 3)
            out.append(Trade(day.strftime("%Y-%m-%dT10:00:00"), day.strftime("%Y-%m-%dT12:00:00"), rng.gauss(mean, sd)))
    return out


def test_classify_concentrates_robust_positive_pair():
    r = ad.evaluate_pair("H3", "GBPUSD", _robust_trades(0.5))
    ad.classify([r])
    assert r.test.n >= 30 and r.positive_windows >= 3
    assert r.verdict == ad.CONCENTRATE and r.p_pos_holm <= 0.05


def test_classify_demotes_positive_but_not_walk_forward_robust():
    trades = [t for t in _robust_trades(0.5) if not t.entry_time.startswith(("2019", "2021"))]  # seulement W3, W4
    r = ad.evaluate_pair("H3", "GBPUSD", trades)
    ad.classify([r])
    assert r.positive_windows == 2 and r.verdict == ad.INCONCLUSIVE and "walk-forward" in r.reasons[0]


def test_classify_demotes_when_top3_concentrated():
    base = _robust_trades(0.05, sd=0.05)
    boosted = [Trade(t.entry_time, t.exit_time, 50.0 if i in (150, 151, 152) else t.r) for i, t in enumerate(sorted(base, key=lambda x: x.entry_time))]
    r = ad.evaluate_pair("H3", "GBPUSD", boosted)
    ad.classify([r])
    assert r.verdict == ad.INCONCLUSIVE


def test_classify_insignificant_after_holm_is_inconclusive():
    rs = _norm(40, 0.1, sd=1.0, seed=5)
    r = _pair("H2", "US30", [], rs)
    ad.classify([r])
    assert r.verdict == ad.INCONCLUSIVE and "non significatif" in r.reasons[0]


def test_weekly_r_and_pearson():
    a = ad.weekly_r(_trades([1.0, 1.0, 1.0, 1.0], start="2024-04-01", step_days=7))
    b = ad.weekly_r(_trades([2.0, 2.0, 2.0, 2.0], start="2024-04-01", step_days=7))
    c = ad.weekly_r(_trades([1.0, -1.0, 1.0, -1.0], start="2024-04-01", step_days=7))
    d = ad.weekly_r(_trades([-1.0, 1.0, -1.0, 1.0], start="2024-04-01", step_days=7))
    weeks = sorted(set(a) | set(c))
    assert ad.pearson(a, b, weeks) == 0.0                       # variance nulle : indéfinie -> 0
    assert ad.pearson(c, c, weeks) == pytest.approx(1.0)
    assert ad.pearson(c, d, weeks) == pytest.approx(-1.0)
    assert ad.pearson(a, b, ["2024-04-01"]) == 0.0              # moins de 2 semaines
    assert ad.weekly_r(_trades([1.0], start="2020-01-01")) == {}  # avant la période test


def _res(h, a, verdict, t=1.0):
    st = ad.PeriodStats(40, 0.1, 0, 0, 0.5, 0.5, t, 1.0, 0.3)
    return ad.PairResult(h, a, st, st, {}, 4, verdict=verdict)


def test_build_mapping_remove_add_cap_and_correlation():
    results = [_res("H1", "GBPUSD", ad.INCONCLUSIVE), _res("H1", "USDJPY", ad.REMOVE), _res("H1", "EURUSD", ad.CONCENTRATE, 3.0),
               _res("H1", "GOLD", ad.CONCENTRATE, 2.0), _res("H1", "US30", ad.INCONCLUSIVE)]
    assert ad.build_mapping(["GBPUSD", "USDJPY"], results, {}) == ["EURUSD", "GOLD", "GBPUSD"]
    # EURUSD/GBPUSD corrélés (> 0,6) : le CONCENTRER (priorité) reste, l'actif actuel est écarté
    assert ad.build_mapping(["GBPUSD", "USDJPY"], results, {("GBPUSD", "EURUSD"): 0.8}) == ["EURUSD", "GOLD"]
    # plafond 4 actifs
    many = [_res("H1", a, ad.CONCENTRATE, 5 - i) for i, a in enumerate(["A", "B", "C", "D", "E"])]
    assert ad.build_mapping([], many, {}) == ["A", "B", "C", "D"]
    # aucun verdict : mapping inchangé
    assert ad.build_mapping(["GBPUSD", "USDJPY"], [_res("H1", "GBPUSD", ad.INCONCLUSIVE)], {}) == ["GBPUSD", "USDJPY"]
    # doublon d'actif dans la priorité ignoré
    dup = [_res("H1", "GBPUSD", ad.CONCENTRATE)]
    assert ad.build_mapping(["GBPUSD"], dup, {}) == ["GBPUSD"]


def test_classify_flags_missing_pvalue_as_inconclusive():
    st = ad.PeriodStats(40, None, None, None, None, None, None, 0.0, None)
    r = ad.PairResult("H1", "GOLD", st, st, {}, 0)
    ad.classify([r])
    assert r.verdict == ad.INCONCLUSIVE
