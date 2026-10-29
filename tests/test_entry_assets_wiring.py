from unittest.mock import MagicMock, patch

import pytest

from src.hypothesis_asset_map import ALL_ASSETS, HYPOTHESIS_ENTRY_ASSETS
from src.technical_strategy_executor import signal_generation_assets


def test_signal_generation_assets_none_means_all():
    assert signal_generation_assets(["A", "B", "C"], None) == ["A", "B", "C"]


def test_signal_generation_assets_restricts_and_ignores_unknown():
    assert signal_generation_assets(["A", "B", "C"], ["C", "A", "Z"]) == ["C", "A"]
    assert signal_generation_assets(["A", "B"], []) == []


CASES = [
    ("src.trend_executor", "run_trend_loop", "H1", {"compute_expensive_hours_by_asset": {}}),
    ("src.hypothesis2_executor", "run_hypothesis2_loop", "H2", {}),
    ("src.hypothesis3_executor", "run_hypothesis3_loop", "H3", {"compute_expensive_hours_by_asset": {}}),
    ("src.hypothesis4_executor", "run_hypothesis4_loop", "H4", {"compute_expensive_hours_by_asset": {}}),
    ("src.hypothesis5_executor", "run_hypothesis5_loop", "H5", {"compute_expensive_hours_by_asset": {}}),
]


@pytest.mark.parametrize("module,func,hyp,extra_patches", CASES)
def test_each_executor_restricts_entries_but_manages_all_assets(module, func, hyp, extra_patches):
    import importlib

    mod = importlib.import_module(module)
    patches = [patch(f"{module}.run_technical_strategy_loop")]
    for name, rv in extra_patches.items():
        patches.append(patch(f"{module}.{name}", return_value=rv))
    mocks = [p.start() for p in patches]
    try:
        getattr(mod, func)(MagicMock(), "db.sqlite", interval_seconds=1)
    finally:
        for p in patches:
            p.stop()
    _, kwargs = mocks[0].call_args
    assert kwargs["entry_assets"] == HYPOTHESIS_ENTRY_ASSETS[hyp]
    assert set(kwargs["assets"]) == set(ALL_ASSETS)  # gestion des positions ouvertes inchangée
