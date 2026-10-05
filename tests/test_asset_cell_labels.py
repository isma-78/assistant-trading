"""Étiquetage des cellules (05/10/2026) : aucune restriction sans procédure validée."""

from src.asset_cell_labels import CELL_LABELS, PROCEDURE_VALIDATED, label_for, restricted_assets


def test_no_restriction_while_procedure_not_validated():
    assert not any(PROCEDURE_VALIDATED.values())
    for source in CELL_LABELS:
        assert restricted_assets(source) == ()
        assert len(CELL_LABELS[source]) == 9


def test_labels_match_primary_matrix():
    assert label_for("hypothesis5_v2", "US100") == "retenue"
    assert label_for("hypothesis5_v2", "BTCUSD") == "non retenue"
    assert label_for("hypothesis2_v2", "CHFJPY") == "non classable"
    assert label_for("hypothesis_v2", "CHFJPY") == "non retenue"
    assert label_for("inconnue", "GOLD") == "non classable"


def test_restriction_logic_if_ever_validated(monkeypatch):
    import src.asset_cell_labels as m
    monkeypatch.setitem(m.PROCEDURE_VALIDATED, "hypothesis5_v2", True)
    assert set(m.restricted_assets("hypothesis5_v2")) == {"GOLD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"}
