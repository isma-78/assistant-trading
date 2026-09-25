import pytest

from src import executor


@pytest.fixture(autouse=True)
def _no_leg_placement_delay(monkeypatch):
    monkeypatch.setattr(executor, "LEG_PLACEMENT_DELAY_SECONDS", 0.0)
