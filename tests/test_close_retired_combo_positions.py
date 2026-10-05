"""A5 (bilan du 05/10/2026) : seul un trade du combo H2 retiré encore ouvert est éligible."""

import importlib.util
import os

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "close_retired_combo_positions.py")
_spec = importlib.util.spec_from_file_location("close_retired_combo_positions", _PATH)
crc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crc)


def _row(**overrides):
    row = {"statut": "ouvert", "source": "hypothesis2_v2", "anomalie_technique": "tp1_cloture_totale_broker",
           "deal_id": "D1"}
    row.update(overrides)
    return row


def test_only_open_retired_combo_trades_are_eligible():
    assert crc.eligible(_row())
    assert not crc.eligible(None)
    assert not crc.eligible(_row(statut="ferme"))
    assert not crc.eligible(_row(source="hypothesis3_v2"))
    assert not crc.eligible(_row(anomalie_technique=None))   # trade E1 normal : jamais touché
    assert not crc.eligible(_row(deal_id=None))
    assert crc.TRADE_IDS == (14877, 15948, 15954, 16329)
