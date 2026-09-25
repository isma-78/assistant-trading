"""
Sortie §2.10 en trois positions broker séparées (Option B, 25/09/2026, voir
docs/DECISIONS.md) : Capital.com ignore `size` sur DELETE /positions et
ferme la position entière — chaque palier est donc sa propre position dès
l'entrée. Ces tests couvrent la décision (tailles des paliers) et
l'orchestration (placement, remplissage, TP1/TP2/stop, réconciliation
depuis /history/activity, péremption, arrêt d'urgence).
"""

from unittest.mock import MagicMock

import pytest

from src.capital_client import CapitalApiError
from src.capital_manager import CapitalManager
from src.db import connection_scope, get_connection, init_db
from src.envelope_store import load_or_create_envelope
from src.executor import (
    GHOST_TRADE_STATUS,
    cancel_stale_working_orders,
    check_pending_fills,
    compute_leg_sizes,
    force_close_all_open_trades,
    manage_open_trades,
    open_signal,
    reconcile_ghost_positions,
    uses_split_legs,
)
from src.go_nogo import GoNoGoStatus
from src.risk_engine import AssetSpec, RiskCaps, RiskEngine

WHITELIST = {"GOLD": AssetSpec(symbol="GOLD", min_units=0.01, pip_value_per_unit=0.86)}
SNAPSHOT_100 = {"snapshot": {"bid": 100.0, "offer": 100.2, "marketStatus": "TRADEABLE"}}


def make_engine(whitelist=WHITELIST):
    return RiskEngine(
        caps=RiskCaps(risk_percent_default=2.0, risk_percent_boosted=4.0, envelope_initial=500.0),
        whitelist=whitelist,
    )


def _insert_signal(db_path, tp1=98.0, tp2=96.0, take_profit=None, source="station_x"):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, 'station_x', '2026-09-25T00:00:00Z', 'texte', 'signal')"
        ).lastrowid
        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
            "tp1, tp2, take_profit, confiance, statut, created_at) "
            "VALUES (?, ?, 'GOLD', 'short', 100.0, 100.0, 101.0, ?, ?, ?, 1.0, 'a_valider', '2026-09-25T00:00:00Z')",
            (raw_id, source, tp1, tp2, take_profit),
        ).lastrowid
        return dict(conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone())


def _open(db_path, client, signal_row, whitelist=WHITELIST):
    return open_signal(
        db_path, client, signal_row, make_engine(whitelist), whitelist, CapitalManager(initial_balance=500.0),
        envelope_id=1, confidence_threshold=0.75, go_nogo_status=GoNoGoStatus(allowed=True, reason="ok"),
    )


def _insert_open_leg_trade(db_path, legs=(("tp1", 5.0), ("tp2", 3.0), ("runner", 2.0)), stop=101.0):
    """Trade GOLD short entré à 100, stop 101 (1R = 1 point), TP1 98 / TP2 96,
    une position par palier déjà remplie (pos-tp1/pos-tp2/pos-runner)."""
    signal_row = _insert_signal(db_path)
    total = sum(size for _, size in legs)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, statut, guaranteed_stop) "
            "VALUES (?, 'pos-tp1', 'station_x', 'GOLD', 'demo', 'short', ?, 100.0, 100.0, 101.0, ?, 10.0, 2.0, "
            "'2026-09-25T00:00:00Z', 'ouvert', 1)",
            (signal_row["id"], total, stop),
        ).lastrowid
        for palier, size in legs:
            conn.execute(
                "INSERT INTO trade_legs (trade_id, palier, taille, order_deal_id, position_deal_id, statut, prix_entree_reel) "
                "VALUES (?, ?, ?, ?, ?, 'ouvert', 100.0)",
                (trade_id, palier, size, f"ord-{palier}", f"pos-{palier}"),
            )
    return trade_id


def _envelopes(db_path):
    envelope_id, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    return {("GOLD", "stationx"): manager}, {("GOLD", "stationx"): envelope_id}, manager


def _positions(*deal_ids):
    return [{"position": {"dealId": d}} for d in deal_ids]


def _client_at(price):
    client = MagicMock()
    client.get_market_snapshot.return_value = {"snapshot": {"bid": price, "offer": price, "marketStatus": "TRADEABLE"}}
    client.get_prices.return_value = {"prices": []}
    client.close_position.return_value = {"level": None, "executed_at": None, "confirmation": None}
    return client


def _rows(db_path, sql, params=()):
    conn = get_connection(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# --- Décision -------------------------------------------------------------

def test_uses_split_legs_only_for_two_targets_and_no_fixed_take_profit():
    assert uses_split_legs(98.0, 96.0, None) is True
    assert uses_split_legs(None, None, None) is False  # H5 : 100% trailing
    assert uses_split_legs(98.0, None, None) is False
    assert uses_split_legs(None, None, 97.0) is False  # cible fixe unique


def test_compute_leg_sizes_splits_total_exactly():
    assert compute_leg_sizes(10.0, 1.0) == (5.0, 3.0, 2.0)
    tp1, tp2, runner = compute_leg_sizes(0.05, 0.01)
    assert tp1 + tp2 + runner == pytest.approx(0.05)
    assert min(tp1, tp2, runner) >= 0.01


def test_compute_leg_sizes_none_when_a_leg_would_fall_below_minimum():
    assert compute_leg_sizes(0.03, 0.01) is None  # 2 / 1 / 0 pas
    assert compute_leg_sizes(0.02, 0.01) is None


# --- Placement ------------------------------------------------------------

def test_open_signal_places_three_orders_and_records_legs(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100
    client.place_limit_order.side_effect = [{"deal_id": "o1"}, {"deal_id": "o2"}, {"deal_id": "o3"}]

    assert _open(db_path, client, _insert_signal(db_path)) == "o1"

    calls = [c.kwargs for c in client.place_limit_order.call_args_list]
    assert len(calls) == 3
    assert {c["level"] for c in calls} == {100.0}
    trade = _rows(db_path, "SELECT * FROM trades")[0]
    legs = _rows(db_path, "SELECT * FROM trade_legs ORDER BY id")
    assert [l["palier"] for l in legs] == ["tp1", "tp2", "runner"]
    assert [l["order_deal_id"] for l in legs] == ["o1", "o2", "o3"]
    assert all(l["statut"] == "en_attente" for l in legs)
    assert sum(l["taille"] for l in legs) == pytest.approx(trade["taille_initiale"])
    assert [c["size"] for c in calls] == [l["taille"] for l in legs]


def test_open_signal_single_order_without_targets(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100
    client.place_limit_order.return_value = {"deal_id": "o1"}

    _open(db_path, client, _insert_signal(db_path, tp1=None, tp2=None))

    assert client.place_limit_order.call_count == 1
    assert _rows(db_path, "SELECT * FROM trade_legs") == []


def test_open_signal_rejects_when_three_legs_impossible(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    coarse = {"GOLD": AssetSpec(symbol="GOLD", min_units=5.0, pip_value_per_unit=0.86)}  # 11.62 -> 10 = 2 pas
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100

    assert _open(db_path, client, _insert_signal(db_path), whitelist=coarse) is None

    client.place_limit_order.assert_not_called()
    decision = _rows(db_path, "SELECT approved, detail FROM risk_decisions")[0]
    assert decision["approved"] == 0
    assert "trois paliers" in decision["detail"]
    assert _rows(db_path, "SELECT * FROM trades") == []


def test_open_signal_leg_failure_cancels_already_placed_legs(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100
    client.place_limit_order.side_effect = [{"deal_id": "o1"}, CapitalApiError("boom")]

    assert _open(db_path, client, _insert_signal(db_path)) is None

    client.cancel_working_order.assert_called_once_with("o1")
    assert _rows(db_path, "SELECT statut FROM trades")[0]["statut"] == "annule"
    assert _rows(db_path, "SELECT * FROM trade_legs") == []


# --- Remplissage ----------------------------------------------------------

def test_check_pending_fills_matches_each_leg_to_its_position(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100
    client.place_limit_order.side_effect = [{"deal_id": "o1"}, {"deal_id": "o2"}, {"deal_id": "o3"}]
    _open(db_path, client, _insert_signal(db_path))

    client.get_working_orders.return_value = [{"workingOrderData": {"dealId": "o3"}}]  # runner pas encore rempli
    client.get_open_positions.return_value = [
        {"position": {"dealId": "p1", "workingOrderId": "o1", "level": 100.0}},
        {"position": {"dealId": "p2", "workingOrderId": "o2", "level": 100.1}},
    ]
    assert check_pending_fills(db_path, client) == 1

    trade = _rows(db_path, "SELECT * FROM trades")[0]
    legs = _rows(db_path, "SELECT * FROM trade_legs ORDER BY id")
    assert trade["statut"] == "ouvert"
    assert [l["statut"] for l in legs] == ["ouvert", "ouvert", "en_attente"]
    assert [l["position_deal_id"] for l in legs[:2]] == ["p1", "p2"]
    expected = (legs[0]["taille"] * 100.0 + legs[1]["taille"] * 100.1) / (legs[0]["taille"] + legs[1]["taille"])
    assert trade["prix_entree_reel"] == pytest.approx(expected)

    client.get_working_orders.return_value = []
    client.get_open_positions.return_value.append({"position": {"dealId": "p3", "workingOrderId": "o3", "level": 100.0}})
    assert check_pending_fills(db_path, client) == 0  # trade déjà ouvert, seul le palier est rapproché
    assert _rows(db_path, "SELECT statut FROM trade_legs WHERE palier = 'runner'")[0]["statut"] == "ouvert"


# --- Gestion --------------------------------------------------------------

def test_tp1_closes_only_tp1_position_and_moves_remaining_stops_to_breakeven(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trade_id = _insert_open_leg_trade(db_path)
    managers, ids, _ = _envelopes(db_path)
    client = _client_at(97.9)  # short : TP1=98 touché

    manage_open_trades(db_path, client, make_engine(), managers, ids)

    client.close_position.assert_called_once()
    assert client.close_position.call_args.args == ("pos-tp1",)
    assert "size" not in client.close_position.call_args.kwargs
    stop_calls = client.update_position_stop.call_args_list
    assert sorted(c.args[0] for c in stop_calls) == ["pos-runner", "pos-tp2"]
    assert all(c.args[1] == 100.0 for c in stop_calls)  # breakeven
    partial = _rows(db_path, "SELECT palier, fraction FROM trade_partials")[0]
    assert (partial["palier"], partial["fraction"]) == ("tp1", pytest.approx(0.5))
    trade = _rows(db_path, "SELECT statut, stop_loss_courant FROM trades WHERE id = ?", (trade_id,))[0]
    assert trade["statut"] == "ouvert"
    assert trade["stop_loss_courant"] == 100.0
    assert [l["statut"] for l in _rows(db_path, "SELECT statut FROM trade_legs ORDER BY id")] == ["ferme", "ouvert", "ouvert"]


def test_stop_after_tp1_closes_remaining_legs_and_books_weighted_r(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trade_id = _insert_open_leg_trade(db_path)
    managers, ids, manager = _envelopes(db_path)

    manage_open_trades(db_path, _client_at(97.9), make_engine(), managers, ids)  # TP1 : +2R sur 50%
    client = _client_at(100.1)  # remonte au-dessus du breakeven (100) : stop touché
    manage_open_trades(db_path, client, make_engine(), managers, ids)

    assert sorted(c.args[0] for c in client.close_position.call_args_list) == ["pos-runner", "pos-tp2"]
    trade = _rows(db_path, "SELECT * FROM trades WHERE id = ?", (trade_id,))[0]
    assert trade["statut"] == "ferme"
    assert trade["r_multiple_total"] == pytest.approx(0.5 * 2.0 + 0.3 * 0.0 + 0.2 * 0.0)
    assert trade["pnl_net"] == pytest.approx(10.0)
    assert manager.balance > 500.0
    assert all(l["statut"] == "ferme" for l in _rows(db_path, "SELECT statut FROM trade_legs"))


def test_stop_urgence_closes_every_open_leg(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_open_leg_trade(db_path)
    managers, ids, _ = _envelopes(db_path)
    client = _client_at(100.5)

    assert force_close_all_open_trades(db_path, client, managers, ids) == 1

    assert sorted(c.args[0] for c in client.close_position.call_args_list) == ["pos-runner", "pos-tp1", "pos-tp2"]
    assert _rows(db_path, "SELECT statut FROM trades")[0]["statut"] == "ferme"


def test_stop_urgence_cancels_every_pending_leg_order(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100
    client.place_limit_order.side_effect = [{"deal_id": "o1"}, {"deal_id": "o2"}, {"deal_id": "o3"}]
    _open(db_path, client, _insert_signal(db_path))
    managers, ids, _ = _envelopes(db_path)

    force_close_all_open_trades(db_path, client, managers, ids)

    assert sorted(c.args[0] for c in client.cancel_working_order.call_args_list) == ["o1", "o2", "o3"]
    assert _rows(db_path, "SELECT statut FROM trades")[0]["statut"] == "annule"


# --- Péremption -----------------------------------------------------------

def _stale(deal_id):
    return {"workingOrderData": {"dealId": deal_id, "createdDateUTC": "2026-01-01T00:00:00"}}


def test_stale_legs_all_unfilled_cancel_the_trade(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = MagicMock()
    client.get_market_snapshot.return_value = SNAPSHOT_100
    client.place_limit_order.side_effect = [{"deal_id": "o1"}, {"deal_id": "o2"}, {"deal_id": "o3"}]
    _open(db_path, client, _insert_signal(db_path))
    client.get_working_orders.return_value = [_stale("o1"), _stale("o2"), _stale("o3")]

    assert cancel_stale_working_orders(db_path, client, max_age_seconds=60) == 3

    trade = _rows(db_path, "SELECT statut, annulation_motif FROM trades")[0]
    assert (trade["statut"], trade["annulation_motif"]) == ("annule", "peremption_marche")


def test_stale_leg_after_partial_fill_shrinks_trade_to_filled_size(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trade_id = _insert_open_leg_trade(db_path)
    with connection_scope(db_path) as conn:
        conn.execute("UPDATE trade_legs SET statut = 'en_attente', position_deal_id = NULL WHERE palier = 'runner'")
    client = MagicMock()
    client.get_working_orders.return_value = [_stale("ord-runner")]

    cancel_stale_working_orders(db_path, client, max_age_seconds=60)

    trade = _rows(db_path, "SELECT statut, taille_initiale, risque_eur FROM trades WHERE id = ?", (trade_id,))[0]
    assert trade["statut"] == "ouvert"
    assert trade["taille_initiale"] == pytest.approx(8.0)
    assert trade["risque_eur"] == pytest.approx(8.0)  # 10€ × 8/10 — le risque ne peut que baisser


# --- Réconciliation depuis /history/activity --------------------------------

def _history(*closes):
    return {"activities": [
        {"dealId": deal_id, "type": "POSITION", "source": "SL", "dateUTC": "2026-09-25T10:00:00",
         "details": {"openPrice": 100.0, "level": level}}
        for deal_id, level in closes
    ]}


def test_reconcile_books_broker_stopped_legs_at_real_price(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trade_id = _insert_open_leg_trade(db_path)
    managers, ids, manager = _envelopes(db_path)
    client = MagicMock()
    client.get_open_positions.return_value = []
    client.get.return_value = _history(("pos-tp1", 101.2), ("pos-tp2", 101.2), ("pos-runner", 101.2))

    assert reconcile_ghost_positions(db_path, client, envelope_managers=managers, envelope_ids=ids) == 1

    trade = _rows(db_path, "SELECT * FROM trades WHERE id = ?", (trade_id,))[0]
    assert trade["statut"] == "ferme"
    assert trade["r_multiple_total"] == pytest.approx(-1.2)  # short 100 -> 101.2, 1R = 1 point
    assert manager.balance == pytest.approx(488.0)
    partials = _rows(db_path, "SELECT palier, prix_sortie_reel FROM trade_partials")
    assert {p["palier"] for p in partials} == {"sl"}
    assert all(p["prix_sortie_reel"] == 101.2 for p in partials)


def test_reconcile_single_position_uses_real_close_instead_of_ghost(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, tp1=None, tp2=None)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, statut) VALUES (?, 'pos-x', 'station_x', 'GOLD', 'demo', 'short', 1.0, 100.0, 101.0, "
            "101.0, 10.0, 2.0, '2026-09-25T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        ).lastrowid
    managers, ids, _ = _envelopes(db_path)
    client = MagicMock()
    client.get_open_positions.return_value = []
    client.get.return_value = _history(("pos-x", 97.0))

    reconcile_ghost_positions(db_path, client, envelope_managers=managers, envelope_ids=ids)

    trade = _rows(db_path, "SELECT statut, r_multiple_total FROM trades WHERE id = ?", (trade_id,))[0]
    assert trade["statut"] == "ferme"
    assert trade["r_multiple_total"] == pytest.approx(3.0)


def test_reconcile_without_history_match_keeps_ghost_semantics(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trade_id = _insert_open_leg_trade(db_path)
    managers, ids, _ = _envelopes(db_path)
    client = MagicMock()
    client.get_open_positions.return_value = []
    client.get.return_value = {"activities": []}

    reconcile_ghost_positions(db_path, client, envelope_managers=managers, envelope_ids=ids)

    trade = _rows(db_path, "SELECT statut, r_multiple_total FROM trades WHERE id = ?", (trade_id,))[0]
    assert trade["statut"] == GHOST_TRADE_STATUS
    assert trade["r_multiple_total"] is None


def test_reconcile_leaves_legs_alone_while_positions_exist(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _insert_open_leg_trade(db_path)
    managers, ids, _ = _envelopes(db_path)
    client = MagicMock()
    client.get_open_positions.return_value = _positions("pos-tp1", "pos-tp2", "pos-runner")

    assert reconcile_ghost_positions(db_path, client, envelope_managers=managers, envelope_ids=ids) == 0
    client.get.assert_not_called()


def test_reconcile_without_envelopes_keeps_historical_ghost_behaviour(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    signal_row = _insert_signal(db_path, tp1=None, tp2=None)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, statut) VALUES (?, 'pos-x', 'station_x', 'GOLD', 'demo', 'short', 1.0, 100.0, 101.0, "
            "101.0, 10.0, 2.0, '2026-09-25T00:00:00Z', 'ouvert')",
            (signal_row["id"],),
        )
    client = MagicMock()
    client.get_open_positions.return_value = []

    reconcile_ghost_positions(db_path, client)

    client.get.assert_not_called()
    assert _rows(db_path, "SELECT statut FROM trades")[0]["statut"] == GHOST_TRADE_STATUS
