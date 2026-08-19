from datetime import datetime, timezone
from unittest.mock import patch

from src.capital_manager import apply_trade_result
from src.circuit_breaker_store import trigger_manual_pause, trigger_stop_urgence
from src.dashboard import generate_dashboard_html
from src.db import connection_scope, init_db
from src.envelope_store import load_or_create_envelope, persist_trade_result

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _close_trade(db_path, actif="GOLD", source="stationx", r_multiple=1.5, pnl_net=15.0, ferme_at=None):
    ferme_at = ferme_at or NOW.isoformat()
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, "
            "ouvert_at, ferme_at, r_multiple_total, pnl_net, statut) "
            "VALUES (?, ?, 'demo', 'long', 0.01, 1.0, 1.0, 10.0, 2.0, ?, ?, ?, ?, 'ferme')",
            (source, actif, ferme_at, ferme_at, r_multiple, pnl_net),
        )


def test_generate_dashboard_html_empty_system(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    output = generate_dashboard_html(db_path, NOW)

    assert "<!doctype html>" in output
    assert "Vue d'ensemble" in output
    assert "Par actif" in output
    assert "Périodes" in output
    assert "Trades" in output
    assert "Hypothèses" in output
    assert "Classement" in output
    assert "Décisions" in output
    assert "Historique complet" in output
    assert "confidence_scorer" in output  # bloc classement clairement étiqueté non construit
    assert "allocator" in output
    assert "hypothesis_engine" in output


def test_generate_dashboard_html_no_external_resources(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    output = generate_dashboard_html(db_path, NOW)
    assert "http://" not in output
    assert "https://" not in output
    assert "<link " not in output
    assert "<script src=" not in output


def test_generate_dashboard_html_shows_populated_asset_metrics(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    _close_trade(db_path, "GOLD", "stationx", r_multiple=2.0, pnl_net=20.0)

    output = generate_dashboard_html(db_path, NOW)
    assert "GOLD" in output
    assert "stationx" in output


def test_generate_dashboard_html_shows_envelope_credited_pnl(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    envelope_id, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('stationx', 'GOLD', 'demo', 'long', 0.01, 1.0, 1.0, 10.0, 2.0, '2026-08-20T00:00:00Z', 'ferme')"
        ).lastrowid
    balance_before = manager.balance
    reserve_share, reserve_after = apply_trade_result(manager, 20.0, reserve_total_before=0.0)
    persist_trade_result(db_path, envelope_id, manager, trade_id, balance_before, reserve_share, reserve_after)

    output = generate_dashboard_html(db_path, NOW)
    assert "10.00 €" in output  # 50% de 20€ crédité à l'enveloppe, pas 20€


def test_generate_dashboard_html_shows_losing_period_pnl(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    envelope_id, manager = load_or_create_envelope(db_path, "GOLD", "demo", 500.0, source="stationx")
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('stationx', 'GOLD', 'demo', 'long', 0.01, 1.0, 1.0, 10.0, 2.0, '2026-08-20T00:00:00Z', 'ferme')"
        ).lastrowid
    balance_before = manager.balance
    reserve_share, reserve_after = apply_trade_result(manager, -10.0, reserve_total_before=0.0)
    persist_trade_result(db_path, envelope_id, manager, trade_id, balance_before, reserve_share, reserve_after)

    output = generate_dashboard_html(db_path, NOW)
    assert 'class=\'neg\'' in output


def test_generate_dashboard_html_shows_hypothesis_progress(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "EURUSD", "hypothesis", r_multiple=1.0)
    output = generate_dashboard_html(db_path, NOW)
    assert "1 / 10 trades" in output


def test_generate_dashboard_html_shows_active_global_block(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trigger_stop_urgence(db_path, "test")
    output = generate_dashboard_html(db_path, NOW)
    assert "stop_urgence" in output


def test_generate_dashboard_html_shows_active_asset_pause(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    trigger_manual_pause(db_path, "GOLD", "test")
    output = generate_dashboard_html(db_path, NOW)
    assert "1 coupe-circuit(s)" in output


def test_generate_dashboard_html_historique_filter_input_present(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "GOLD", "stationx")
    output = generate_dashboard_html(db_path, NOW)
    assert 'id="filter"' in output
    assert "filterHistorique" in output


def test_generate_dashboard_html_escapes_html_in_data(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    _close_trade(db_path, "<script>alert(1)</script>", "stationx")
    output = generate_dashboard_html(db_path, NOW)
    assert "<script>alert(1)</script>" not in output
    assert "&lt;script&gt;" in output


def test_generate_dashboard_html_resilient_to_block_failure(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    with patch("src.dashboard._render_par_actif", side_effect=RuntimeError("boom")):
        output = generate_dashboard_html(db_path, NOW)
    assert "Bloc indisponible" in output
    assert "<!doctype html>" in output  # le reste de la page est quand même généré


def test_generate_dashboard_html_default_now_when_omitted(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    output = generate_dashboard_html(db_path)  # now=None -> datetime.now(timezone.utc)
    assert "<!doctype html>" in output
