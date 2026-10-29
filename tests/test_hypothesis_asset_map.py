import json
from pathlib import Path

from src.hypothesis_asset_map import ALL_ASSETS, HYPOTHESIS_ENTRY_ASSETS, MAX_ASSETS_PER_HYPOTHESIS, entry_assets_for

ROOT = Path(__file__).resolve().parent.parent
STRUCT = json.loads((ROOT / "data" / "analysis_29-10" / "structural.json").read_text(encoding="utf-8"))
CORR_MAX = 0.60


def _corr(a, b):
    for key in (f"{a}|{b}", f"{b}|{a}"):
        if key in STRUCT["corr_old"]:
            return STRUCT["corr_old"][key], STRUCT["corr_recent"][key]
    raise KeyError((a, b))


def test_five_hypotheses_and_known_assets_only():
    assert set(HYPOTHESIS_ENTRY_ASSETS) == {"H1", "H2", "H3", "H4", "H5"}
    for assets in HYPOTHESIS_ENTRY_ASSETS.values():
        assert set(assets) <= set(ALL_ASSETS)
        assert len(assets) == len(set(assets))


def test_at_most_four_assets_per_hypothesis():
    assert all(len(a) <= MAX_ASSETS_PER_HYPOTHESIS for a in HYPOTHESIS_ENTRY_ASSETS.values())


def test_chfjpy_excluded_from_h2_to_h5():
    for h in ("H2", "H3", "H4", "H5"):
        assert "CHFJPY" not in HYPOTHESIS_ENTRY_ASSETS[h]


def test_every_entry_asset_is_structurally_adapted():
    for h, assets in HYPOTHESIS_ENTRY_ASSETS.items():
        for a in assets:
            assert STRUCT["table"][h][a]["status"] == "adapté", (h, a)


def test_no_highly_correlated_pair_within_a_hypothesis():
    for h, assets in HYPOTHESIS_ENTRY_ASSETS.items():
        for i, a in enumerate(assets):
            for b in assets[i + 1:]:
                assert all(abs(c) <= CORR_MAX for c in _corr(a, b)), (h, a, b)


def test_entry_assets_for_returns_copy_and_unknown_is_empty():
    got = entry_assets_for("H2")
    got.append("GOLD")
    assert entry_assets_for("H2") == ["GBPUSD", "USDJPY"]
    assert entry_assets_for("H9") == []
