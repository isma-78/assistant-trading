"""
Tests de trade_analyzer — partie déterministe (compute_trade_features) et
partie LLM narrative (generate_narrative_summary, garde-fou de sortie).
Aucun appel réseau réel : le client Anthropic est simulé.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.db import connection_scope, get_connection, init_db
from src.trade_analyzer import (
    NarrativeGuardrailError,
    TradeFeatures,
    _check_narrative_guardrail,
    analyze_closed_trade,
    compute_trade_features,
    generate_narrative_summary,
)


def _trade_row(**overrides):
    base = dict(
        id=1, signal_id=7, actif="GOLD", ouvert_at="2026-08-16T10:00:00+00:00",
        ferme_at="2026-08-16T10:45:00+00:00", prix_entree_prevu=100.0, prix_entree_reel=100.02,
        r_multiple_total=1.4, pourcentage_risque_applique=2.0,
    )
    base.update(overrides)
    return base


def _partial_row(palier="tp1"):
    return {"palier": palier}


def _signal_row(confiance=1.0):
    return {"confiance": confiance}


# --- compute_trade_features ------------------------------------------------

def test_compute_trade_features_basic_fields():
    features = compute_trade_features(_trade_row(), _partial_row("tp1"), _signal_row(0.9))
    assert features.trade_id == 1
    assert features.signal_id == 7
    assert features.r_multiple_realise == 1.4
    assert features.denouement == "tp1_hit"
    assert features.duree_secondes == 45 * 60
    assert features.ecart_signal_execution == pytest.approx(0.02)
    assert features.confiance_signal == 0.9
    assert features.heure_ouverture == 10
    assert features.boosted is False


def test_compute_trade_features_sl_denouement():
    features = compute_trade_features(_trade_row(r_multiple_total=-1.0), _partial_row("sl"))
    assert features.denouement == "sl_hit"
    assert features.confiance_signal is None  # pas de signal_row fourni


def test_compute_trade_features_boosted_true_above_threshold():
    features = compute_trade_features(_trade_row(pourcentage_risque_applique=4.0), _partial_row("tp3"))
    assert features.boosted is True
    assert features.denouement == "tp3_hit"


def test_compute_trade_features_no_execution_price_no_gap():
    features = compute_trade_features(_trade_row(prix_entree_reel=None), _partial_row("tp1"))
    assert features.ecart_signal_execution is None


def test_compute_trade_features_weekday_computed_from_ouvert_at():
    # 2026-08-16 est un dimanche -> weekday() = 6
    features = compute_trade_features(_trade_row(), _partial_row("tp1"))
    assert features.jour_semaine == 6


# --- garde-fou de sortie (invariant #9) ------------------------------------

def test_guardrail_accepts_factual_text():
    _check_narrative_guardrail("Le trade sur GOLD a touché TP1 après 45 minutes, réalisant +1.40R.")


def test_guardrail_accepts_neutral_winning_trade_vocabulary():
    # "trade gagnant" est le vocabulaire neutre du CDC lui-même (§2.3 :
    # "Trade GAGNANT sur l'actif X") — un simple constat de résultat,
    # pas un jugement sur la stratégie. Ne doit jamais être bloqué.
    _check_narrative_guardrail("Ce trade gagnant sur GOLD a réalisé +1.40R en 45 minutes.")


@pytest.mark.parametrize("forbidden_text", [
    "Ce trade aurait dû être clôturé plus tôt.",
    "C'était une bonne décision d'entrer sur ce niveau.",
    "Il faudrait resserrer le stop la prochaine fois.",
    "La stratégie s'est révélée gagnante sur ce trade.",
    "Je recommande de reproduire ce type d'entrée.",
])
def test_guardrail_rejects_judgment_language(forbidden_text):
    with pytest.raises(NarrativeGuardrailError):
        _check_narrative_guardrail(forbidden_text)


def _fake_anthropic_response(text: str):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    return response


def test_generate_narrative_summary_returns_clean_text():
    client = MagicMock()
    client.messages.create.return_value = _fake_anthropic_response(
        "Le trade sur GOLD a touché TP1 après 45 minutes, réalisant +1.40R."
    )
    features = compute_trade_features(_trade_row(), _partial_row("tp1"), _signal_row())

    text = generate_narrative_summary(features, client)

    assert "TP1" in text
    client.messages.create.assert_called_once()
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0
    assert "INTERDICTION ABSOLUE" in call_kwargs["system"]


def test_generate_narrative_summary_raises_on_forbidden_content():
    client = MagicMock()
    client.messages.create.return_value = _fake_anthropic_response(
        "Ce trade aurait dû être géré différemment."
    )
    features = compute_trade_features(_trade_row(), _partial_row("tp1"))

    with pytest.raises(NarrativeGuardrailError):
        generate_narrative_summary(features, client)


# --- analyze_closed_trade (orchestration, DB réelle temporaire) -----------

def _seed_closed_trade(db_path, r_multiple=1.4, palier="tp1", pourcentage=2.0):
    with connection_scope(db_path) as conn:
        raw_id = conn.execute(
            "INSERT INTO raw_messages (telegram_msg_id, channel, received_at, raw_text, message_type) "
            "VALUES (1, 'station_x', '2026-08-16T00:00:00Z', 'texte', 'signal')"
        ).lastrowid
        signal_id = conn.execute(
            "INSERT INTO signals (raw_message_id, source, actif, sens, entree_min, entree_max, stop_loss, "
            "confiance, statut, created_at) "
            "VALUES (?, 'station_x', 'GOLD', 'short', 100.0, 100.0, 101.0, 1.0, 'approuve', '2026-08-16T00:00:00Z')",
            (raw_id,),
        ).lastrowid
        trade_id = conn.execute(
            "INSERT INTO trades (signal_id, deal_id, source, actif, mode, direction, taille_initiale, "
            "prix_entree_prevu, prix_entree_reel, stop_loss_initial, stop_loss_courant, risque_eur, "
            "pourcentage_risque_applique, ouvert_at, ferme_at, r_multiple_total, pnl_net, statut) "
            "VALUES (?, 'deal-1', 'station_x', 'GOLD', 'demo', 'short', 0.01, 100.0, 100.0, 101.0, 101.0, 10.0, "
            "?, '2026-08-16T10:00:00+00:00', '2026-08-16T10:45:00+00:00', ?, 14.0, 'ferme')",
            (signal_id, pourcentage, r_multiple),
        ).lastrowid
        conn.execute(
            "INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at) "
            "VALUES (?, ?, 1.0, 98.6, ?, 'test', '2026-08-16T10:45:00Z')",
            (trade_id, palier, r_multiple),
        )
    return trade_id


def test_analyze_closed_trade_without_llm_client_persists_deterministic_only(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _seed_closed_trade(db_path)

    features = analyze_closed_trade(db_path, trade_id, anthropic_client=None)

    assert features.r_multiple_realise == 1.4
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM trade_analysis WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["r_multiple_realise"] == 1.4
        assert row["resume_narratif"] is None
    finally:
        conn.close()


@patch("src.trade_analyzer.send_notification")
def test_analyze_closed_trade_with_llm_persists_and_notifies(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _seed_closed_trade(db_path)

    client = MagicMock()
    client.messages.create.return_value = _fake_anthropic_response(
        "Le trade sur GOLD a touché TP1 après 45 minutes, réalisant +1.40R."
    )

    analyze_closed_trade(db_path, trade_id, anthropic_client=client, bot_token="tok", chat_id="42")

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM trade_analysis WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["resume_narratif"] is not None
        assert "TP1" in row["resume_narratif"]
    finally:
        conn.close()
    mock_notify.assert_called_once()


@patch("src.trade_analyzer.send_notification")
def test_analyze_closed_trade_guardrail_rejection_keeps_deterministic_part_only(mock_notify, tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    trade_id = _seed_closed_trade(db_path)

    client = MagicMock()
    client.messages.create.return_value = _fake_anthropic_response("C'était une bonne décision.")

    features = analyze_closed_trade(db_path, trade_id, anthropic_client=client, bot_token="tok", chat_id="42")

    assert features.r_multiple_realise == 1.4  # partie déterministe toujours calculée
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM trade_analysis WHERE trade_id = ?", (trade_id,)).fetchone()
        assert row["resume_narratif"] is None  # rejeté par le garde-fou, jamais stocké
    finally:
        conn.close()
    mock_notify.assert_not_called()  # jamais notifié non plus


def test_analyze_closed_trade_not_found_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with pytest.raises(ValueError):
        analyze_closed_trade(db_path, trade_id=999, anthropic_client=None)


def test_analyze_closed_trade_not_yet_closed_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with connection_scope(db_path) as conn:
        trade_id = conn.execute(
            "INSERT INTO trades (source, actif, mode, direction, taille_initiale, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut) "
            "VALUES ('station_x', 'GOLD', 'demo', 'short', 0.01, 101.0, 101.0, 10.0, 2.0, "
            "'2026-08-16T10:00:00Z', 'ouvert')"
        ).lastrowid
    with pytest.raises(ValueError):
        analyze_closed_trade(db_path, trade_id, anthropic_client=None)
