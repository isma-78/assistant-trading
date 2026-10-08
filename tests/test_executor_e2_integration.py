"""
E2 branché sur le seul point d'appel du resserrement (`_push_stop_to_broker`,
étape 3 du mandat de reprise du 08/10/2026, voir docs/DECISIONS.md et
docs/PATCH_EXECUTOR_E2_PROPOSE.diff). Interrupteur OFF par défaut
(`system_state.e2_enabled`) : ces tests couvrent spécifiquement le
POINT D'INTÉGRATION (pas le module `stop_tightening_retry` isolé, déjà
testé ailleurs) -- impossibilité d'élargir un stop même si le
`risk_engine` fourni se trompe, et repli sur une seule tentative directe
en cas d'erreur inattendue du module E2 lui-même.
"""

from unittest.mock import MagicMock

import pytest

from src.db import connection_scope, init_db
from src.execution.stop_tightening_retry import StopWideningBlocked
from src.executor import OpenTradeState, _push_stop_to_broker
from src.risk_engine import RiskDecision


def _enable_e2(db_path):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('e2_enabled', 'true', '2026-10-08T00:00:00+00:00')"
        )


def _state(stop_price=101.0, direction="short", guaranteed_stop=False):
    return OpenTradeState(
        trade_id=1, deal_id="pos-1", asset="GOLD", source="hypothesis2_v2", direction=direction,
        entry_price=100.0, initial_stop_price=101.0, stop_price=stop_price,
        tp1=98.0, tp2=96.0, tp1_hit=False, tp2_hit=False, remaining_fraction=1.0,
        guaranteed_stop=guaranteed_stop,
    )


def _insert_trade(db_path, trade_id, stop):
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (id, deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (?, 'pos-1', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 1.0, 101.0, ?, 10.0, 2.0, "
            "'2026-10-08T00:00:00+00:00', 'ouvert')",
            (trade_id, stop),
        )


def test_e2_off_by_default_behaves_like_a_single_direct_call(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_trade(db_path, 1, 101.0)
    client = MagicMock()
    risk_engine = MagicMock()
    state = _state(stop_price=101.0)

    # Resserrement légitime (short : stop plus bas = plus protecteur).
    _push_stop_to_broker(db_path, client, state, candidate_stop_price=99.0, risk_engine=risk_engine)

    client.update_position_stop.assert_called_once()
    risk_engine.evaluate_stop_update.assert_not_called()  # jamais sollicité par le module E2 quand il est OFF
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = 1").fetchone()
    assert row["stop_loss_courant"] == pytest.approx(99.0)


def test_e2_on_blocks_a_widening_even_if_risk_engine_wrongly_approves_it(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_trade(db_path, 1, 101.0)
    _enable_e2(db_path)
    client = MagicMock()
    # `risk_engine` se trompe et approuve un candidat MOINS protecteur
    # (102.0 > 101.0 en short = élargissement) -- le garde-fou redondant
    # de `_retry_guarded` doit bloquer quand même, jamais s'appuyer
    # seulement sur le jugement de l'appelant.
    risk_engine = MagicMock()
    risk_engine.evaluate_stop_update.return_value = RiskDecision(approved=True)
    state = _state(stop_price=101.0)

    with pytest.raises(StopWideningBlocked):
        _push_stop_to_broker(db_path, client, state, candidate_stop_price=102.0, risk_engine=risk_engine)

    client.update_position_stop.assert_not_called()
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = 1").fetchone()
    # Le stop en base n'a JAMAIS été élargi -- inchangé après le blocage.
    assert row["stop_loss_courant"] == pytest.approx(101.0)


def test_e2_on_falls_back_to_a_single_attempt_on_unexpected_module_error(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_trade(db_path, 1, 101.0)
    _enable_e2(db_path)
    client = MagicMock()
    # Erreur INATTENDUE du module E2 lui-même (pas une erreur broker
    # normale) -- `attempt_with_retry` doit se replier sur un seul appel
    # direct (comportement d'avant E2), jamais laisser tomber le
    # resserrement.
    risk_engine = MagicMock()
    risk_engine.evaluate_stop_update.side_effect = RuntimeError("bug imprévu du module E2")
    state = _state(stop_price=101.0)

    _push_stop_to_broker(db_path, client, state, candidate_stop_price=99.0, risk_engine=risk_engine)

    client.update_position_stop.assert_called_once()
    with connection_scope(db_path) as conn:
        row = conn.execute("SELECT stop_loss_courant FROM trades WHERE id = 1").fetchone()
    assert row["stop_loss_courant"] == pytest.approx(99.0)
