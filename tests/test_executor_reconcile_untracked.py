"""
reconcile_untracked_broker_positions (A1, mandat de reprise du
08/10/2026, voir docs/DECISIONS.md) : une position broker sans AUCUNE
trace en base (ni `trades.deal_id`, ni `trade_legs.order_deal_id`/
`position_deal_id`) est retrouvée et enregistrée après un délai de
grâce, jamais avant, jamais en doublon, jamais en élargissant un stop
existant.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src import circuit_breaker_store
from src.circuit_breaker import CORRELATION_CLUSTERS
from src.db import connection_scope, init_db
from src.executor import UNTRACKED_POSITION_GRACE_SECONDS, reconcile_untracked_broker_positions
from src.risk_engine import AssetSpec

WHITELIST = {
    "GOLD": AssetSpec(symbol="GOLD", min_units=0.01, pip_value_per_unit=0.86),
    "US30": AssetSpec(symbol="US30", min_units=0.001, pip_value_per_unit=0.86),
}


def _rows(db_path, sql, params=()):
    with connection_scope(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _old_enough(seconds_ago=UNTRACKED_POSITION_GRACE_SECONDS + 60):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _recent(seconds_ago=60):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _position(deal_id, created, epic="GOLD", direction="SELL", size=0.05, level=2400.0,
              stop_level=2420.0, working_order_id=None, guaranteed_stop=False):
    return {
        "position": {
            "dealId": deal_id, "workingOrderId": working_order_id, "createdDateUTC": created,
            "direction": direction, "size": size, "level": level, "stopLevel": stop_level,
            "guaranteedStop": guaranteed_stop,
        },
        "market": {"epic": epic},
    }


def _client(positions):
    client = MagicMock()
    client.get_open_positions.return_value = positions
    return client


def test_registers_new_trade_for_untracked_position_past_grace_period(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([_position("pos-orphan", _old_enough())])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 1
    trades = _rows(db_path, "SELECT * FROM trades WHERE deal_id = 'pos-orphan'")
    assert len(trades) == 1
    trade = trades[0]
    assert trade["statut"] == "ouvert"
    assert trade["actif"] == "GOLD"
    assert trade["source"] == "hypothesis2_v2"
    assert trade["direction"] == "short"
    assert trade["mode"] == "demo"
    assert trade["stop_loss_initial"] == pytest.approx(2420.0)
    assert trade["stop_loss_courant"] == pytest.approx(2420.0)
    assert trade["risque_eur"] == pytest.approx(abs(2400.0 - 2420.0) * 0.05 * 0.86)


def test_skips_position_within_grace_period(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([_position("pos-recent", _recent())])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 0
    assert _rows(db_path, "SELECT * FROM trades WHERE deal_id = 'pos-recent'") == []


def test_idempotent_when_deal_id_already_known_on_trades(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('pos-known', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.05, 2420.0, 2420.0, 10.0, 2.0, "
            "'2026-01-01T00:00:00+00:00', 'ferme')"
        )
    client = _client([_position("pos-known", _old_enough())])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 0
    assert len(_rows(db_path, "SELECT * FROM trades WHERE deal_id = 'pos-known'")) == 1


def test_idempotent_when_deal_id_already_known_via_trade_legs(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES (NULL, 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.05, 2420.0, 2420.0, 10.0, 2.0, "
            "'2026-01-01T00:00:00+00:00', 'ouvert')"
        ).lastrowid
        conn.execute(
            "INSERT INTO trade_legs (trade_id, palier, taille, position_deal_id, statut) "
            "VALUES (?, 'tp1', 0.05, 'pos-leg-known', 'ouvert')",
            (trade_id,),
        )
    client = _client([_position("pos-leg-known", _old_enough())])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 0


def test_attaches_as_sibling_leg_when_open_trade_nearby(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    created = _old_enough()
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('pos-tp1', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.03, 2420.0, 2420.0, 10.0, 2.0, "
            "?, 'ouvert')",
            (created,),
        ).lastrowid
        conn.execute(
            "INSERT INTO trade_legs (trade_id, palier, taille, position_deal_id, statut) "
            "VALUES (?, 'tp1', 0.03, 'pos-tp1', 'ouvert')",
            (trade_id,),
        )
    # La jambe "tp2" a été placée au broker au même moment que tp1 (même
    # trade), mais sa réponse d'origine s'est perdue -- jamais enregistrée.
    client = _client([
        _position("pos-tp1", created),
        _position("pos-tp2-lost", created, size=0.02),
    ])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 1
    trades = _rows(db_path, "SELECT * FROM trades WHERE id = ?", (trade_id,))
    assert len(trades) == 1
    # Aucun second trade créé pour la même ouverture d'origine.
    assert len(_rows(db_path, "SELECT * FROM trades")) == 1
    legs = _rows(db_path, "SELECT palier, position_deal_id FROM trade_legs WHERE trade_id = ?", (trade_id,))
    assert {leg["palier"] for leg in legs} == {"tp1", "tp2"}
    attached = next(leg for leg in legs if leg["palier"] == "tp2")
    assert attached["position_deal_id"] == "pos-tp2-lost"
    # Le risque est désormais la somme des deux jambes connues (la
    # contribution de la jambe retrouvée est arrondie à 2 décimales,
    # comme tout `risque_eur` ailleurs dans le projet).
    assert trades[0]["risque_eur"] == pytest.approx(10.0 + round(abs(2400.0 - 2420.0) * 0.02 * 0.86, 2))


def test_never_widens_an_existing_stop(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    created = _old_enough()
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('pos-tp1', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.03, 2420.0, 2415.0, 10.0, 2.0, "
            "?, 'ouvert')",
            (created,),
        ).lastrowid
        conn.execute(
            "INSERT INTO trade_legs (trade_id, palier, taille, position_deal_id, statut) "
            "VALUES (?, 'tp1', 0.03, 'pos-tp1', 'ouvert')",
            (trade_id,),
        )
    client = _client([_position("pos-tp1", created), _position("pos-tp2-lost", created, size=0.02)])

    reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    trade = _rows(db_path, "SELECT stop_loss_initial, stop_loss_courant FROM trades WHERE id = ?", (trade_id,))[0]
    # stop_loss_courant (2415.0, déjà resserré) et stop_loss_initial (2420.0)
    # restent EXACTEMENT ce qu'ils étaient -- cette fonction ne les touche jamais.
    assert trade["stop_loss_initial"] == pytest.approx(2420.0)
    assert trade["stop_loss_courant"] == pytest.approx(2415.0)


def test_skips_and_notifies_when_asset_not_in_whitelist(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([_position("pos-unknown-asset", _old_enough(), epic="UNKNOWN")])

    registered = reconcile_untracked_broker_positions(
        db_path, client, "hypothesis2_v2", WHITELIST, bot_token="t", chat_id="c",
    )

    assert registered == 0
    assert _rows(db_path, "SELECT * FROM trades") == []


def test_skips_when_stop_level_missing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([_position("pos-no-stop", _old_enough(), stop_level=None)])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 0
    assert _rows(db_path, "SELECT * FROM trades") == []


def test_skips_when_created_timestamp_missing(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([_position("pos-no-created", None)])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 0


def test_returns_count_across_multiple_unrelated_positions(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([
        _position("pos-a", _old_enough(), epic="GOLD"),
        _position("pos-b", _old_enough(), epic="US30", level=50000.0, stop_level=50100.0),
    ])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 2
    assert len(_rows(db_path, "SELECT * FROM trades")) == 2


def test_notifies_telegram_on_successful_registration(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    client = _client([_position("pos-orphan", _old_enough())])

    registered = reconcile_untracked_broker_positions(
        db_path, client, "hypothesis2_v2", WHITELIST, bot_token="t", chat_id="c",
    )

    assert registered == 1


def test_sibling_search_ignores_sibling_with_unparsable_ouvert_at(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('pos-tp1', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.03, 2420.0, 2420.0, 10.0, 2.0, "
            "'pas-une-date', 'ouvert')"
        )
    client = _client([_position("pos-orphan", _old_enough())])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 1
    # Le trade au `ouvert_at` illisible est ignoré comme jambe sœur candidate
    # (jamais planté) -- un second trade, indépendant, est créé à la place.
    assert len(_rows(db_path, "SELECT * FROM trades")) == 2


def test_sibling_outside_grace_window_is_not_attached(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    far_away = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('pos-tp1', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.03, 2420.0, 2420.0, 10.0, 2.0, "
            "?, 'ouvert')",
            (far_away,),
        )
    client = _client([_position("pos-orphan", _old_enough())])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 1
    assert len(_rows(db_path, "SELECT * FROM trades")) == 2


def test_sibling_already_has_all_three_legs_falls_back_to_new_trade(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    created = _old_enough()
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('pos-tp1', 'hypothesis2_v2', 'GOLD', 'demo', 'short', 0.03, 2420.0, 2420.0, 10.0, 2.0, "
            "?, 'ouvert')",
            (created,),
        ).lastrowid
        for palier in ("tp1", "tp2", "runner"):
            conn.execute(
                "INSERT INTO trade_legs (trade_id, palier, taille, position_deal_id, statut) "
                "VALUES (?, ?, 0.01, ?, 'ouvert')",
                (trade_id, palier, f"pos-{palier}"),
            )
    client = _client([_position("pos-orphan", created)])

    registered = reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    assert registered == 1
    assert len(_rows(db_path, "SELECT * FROM trades")) == 2
    assert len(_rows(db_path, "SELECT * FROM trade_legs WHERE trade_id = ?", (trade_id,))) == 3


def test_parse_broker_timestamp_assumes_utc_when_naive():
    from src.executor import _parse_broker_timestamp

    # Certains horodatages broker (`createdDateUTC`) n'ont ni 'Z' ni offset
    # explicite -- toujours interprétés comme UTC (le nom du champ le dit).
    parsed = _parse_broker_timestamp("2026-10-07T20:19:27.582000")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_cluster_cap_correctly_reflects_reconciled_risk_no_doublecount(tmp_path):
    """Non-doublon sur le plafond de cluster (§A1) : après réconciliation,
    `get_cluster_open_risk_eur` reflète exactement le risque de la position
    retrouvée, ni compté deux fois, ni absent."""
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    cluster_assets = [a for a, c in CORRELATION_CLUSTERS.items() if c == CORRELATION_CLUSTERS["GOLD"]]
    assert circuit_breaker_store.get_cluster_open_risk_eur(db_path, cluster_assets) == 0.0

    client = _client([_position("pos-orphan", _old_enough())])
    reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)

    expected_risk = abs(2400.0 - 2420.0) * 0.05 * 0.86
    assert circuit_breaker_store.get_cluster_open_risk_eur(db_path, cluster_assets) == pytest.approx(expected_risk)

    # Rejouer la réconciliation (ex. cycle suivant) ne doit jamais compter
    # la même position deux fois.
    reconcile_untracked_broker_positions(db_path, client, "hypothesis2_v2", WHITELIST)
    assert circuit_breaker_store.get_cluster_open_risk_eur(db_path, cluster_assets) == pytest.approx(expected_risk)
