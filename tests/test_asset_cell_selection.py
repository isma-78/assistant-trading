"""Protocole pré-enregistré du 05/10/2026 (commit 415dbb5) : classement des cellules et walk-forward."""

import pytest

from src.asset_cell_selection import (
    NOT_CLASSIFIABLE,
    NOT_RETAINED,
    RETAINED,
    CellTrade,
    block_bootstrap_lower_bound,
    classify_cells,
    group_difference,
    test_group_returns as split_test_groups,
    walk_forward,
)

PERIOD = ("2019-01-01", "2023-01-01")
HALVES = (("2019-01-01", "2021-01-01"), ("2021-01-01", "2023-01-01"))


def _trades(asset, r_first, r_second, n_each, start_years=("2019", "2021")):
    out = []
    for i in range(n_each):
        out.append(CellTrade("H", asset, f"{start_years[0]}-0{1 + i % 9}-{1 + i % 27:02d}T00:00:00", r_first))
        out.append(CellTrade("H", asset, f"{start_years[1]}-0{1 + i % 9}-{1 + i % 27:02d}T00:00:00", r_second))
    return out


def test_classification_rules_and_shrinkage():
    trades = (_trades("A", 0.5, 0.5, 60) + _trades("B", 0.5, -0.2, 60) + _trades("C", -0.3, -0.3, 60)
              + _trades("D", 1.0, 1.0, 10) + _trades("X", 2.0, 2.0, 60))
    cells = classify_cells(trades, ["A", "B", "C", "D", "E", "X"], PERIOD, HALVES, excluded_assets=["X"])
    assert cells["A"].status == RETAINED
    assert cells["B"].status == NOT_RETAINED          # signe instable
    assert cells["C"].status == NOT_RETAINED
    assert cells["D"].status == NOT_CLASSIFIABLE and "20 < 100" in cells["D"].reason
    assert cells["E"].status == NOT_CLASSIFIABLE and cells["E"].n == 0 and cells["E"].contracted is None
    assert cells["X"].status == NOT_CLASSIFIABLE and "décision 4" in cells["X"].reason
    hyp_mean = sum(t.r for t in trades) / len(trades)
    w = 120 / 220
    assert cells["A"].contracted == pytest.approx(w * 0.5 + (1 - w) * hyp_mean)


def test_negative_both_halves_with_positive_contraction_is_not_retained():
    """Lecture prudente pré-enregistrée : « identique ET positif »."""
    trades = _trades("A", -0.01, -0.01, 60) + _trades("Y", 5.0, 5.0, 60)
    cells = classify_cells(trades, ["A"], PERIOD, HALVES)
    assert cells["A"].contracted > 0 and cells["A"].status == NOT_RETAINED


def test_group_split_and_difference():
    cells = classify_cells(_trades("A", 1, 1, 60) + _trades("B", -1, -1, 60) + _trades("C", 1, 1, 5),
                           ["A", "B", "C"], PERIOD, HALVES)
    test = [CellTrade("H", "A", "2022-03-01", 0.4), CellTrade("H", "B", "2022-03-02", -0.1),
            CellTrade("H", "C", "2022-03-03", 9.0), CellTrade("H", "Z", "2022-03-03", 9.0),
            CellTrade("H", "A", "2018-03-03", 9.0)]
    retained, others = split_test_groups(test, cells, ("2022-01-01", "2023-01-01"))
    assert [t.asset for t in retained] == ["A"] and [t.asset for t in others] == ["B"]
    assert group_difference(retained, others) == pytest.approx(0.5)
    assert group_difference([], others) is None


def test_block_bootstrap_lower_bound():
    labelled = [(CellTrade("H", "A", f"2021-{m:02d}-03", 1.0 + 0.1 * (m % 3)), True) for m in range(1, 13)]
    labelled += [(CellTrade("H", "B", f"2021-{m:02d}-10", -1.0), False) for m in range(1, 13)]
    lower, invalid = block_bootstrap_lower_bound(labelled, 0.01, reps=500)
    assert lower > 1.5 and invalid >= 0
    assert block_bootstrap_lower_bound([], 0.01, reps=10) == (None, 10)
    only_retained = [(CellTrade("H", "A", "2021-01-04", 1.0), True)]
    assert block_bootstrap_lower_bound(only_retained, 0.01, reps=10) == (None, 10)


def _yearly(asset, r_by_year, per_year=120):
    out = []
    for year, r in r_by_year.items():
        for i in range(per_year):
            out.append(CellTrade("H", asset, f"{year}-{1 + i % 12:02d}-{1 + i % 27:02d}T00:00:00", r + 0.01 * (i % 5)))
    return out


def test_walk_forward_validates_persistent_asset_effect():
    trades = (_yearly("GOOD", {2019: 0.3, 2020: 0.3, 2021: 0.3, 2022: 0.3})
              + _yearly("BAD", {2019: -0.3, 2020: -0.3, 2021: -0.3, 2022: -0.3}))
    result = walk_forward("H", trades, ["GOOD", "BAD"])
    assert result.validated and result.diff_a == pytest.approx(0.6, abs=0.05) and result.lower_bound > 0


def test_walk_forward_not_validated_when_effect_flips_or_groups_empty():
    flip = (_yearly("A", {2019: 0.3, 2020: 0.3, 2021: -0.3, 2022: -0.3})
            + _yearly("B", {2019: -0.3, 2020: -0.3, 2021: 0.3, 2022: 0.3}))
    assert not walk_forward("H", flip, ["A", "B"]).validated
    none_retained = _yearly("A", {2019: -0.3, 2020: -0.3, 2021: -0.3, 2022: -0.3})
    result = walk_forward("H", none_retained, ["A"])
    assert not result.validated and "groupe vide" in result.reason


def test_walk_forward_not_validated_when_second_fold_reverses():
    trades = (_yearly("A", {2019: 0.3, 2020: 0.3, 2021: 0.3, 2022: -0.3})
              + _yearly("B", {2019: -0.3, 2020: -0.3, 2021: -0.3, 2022: 0.3}))
    result = walk_forward("H", trades, ["A", "B"])
    assert result.diff_a > 0 and result.diff_b < 0
    assert not result.validated and "non remplie" in result.reason
