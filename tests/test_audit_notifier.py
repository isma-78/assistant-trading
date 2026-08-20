"""
Tests d'audit_notifier — envoi de notifications au bot de contrôle (§7.2,
§3.6). Aucun appel réseau réel : urlopen est simulé.
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from src.audit_notifier import (
    format_matinale_notification,
    format_signal_notification,
    send_document,
    send_notification,
)


def _fake_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


@patch("src.audit_notifier.urllib.request.urlopen")
def test_send_notification_returns_true_on_telegram_ok(mock_urlopen):
    mock_urlopen.return_value = _fake_response({"ok": True, "result": {}})
    assert send_notification("fake-token", "12345", "test") is True


@patch("src.audit_notifier.urllib.request.urlopen")
def test_send_notification_returns_false_on_telegram_error_payload(mock_urlopen):
    mock_urlopen.return_value = _fake_response({"ok": False, "description": "bad chat"})
    assert send_notification("fake-token", "12345", "test") is False


@patch("src.audit_notifier.urllib.request.urlopen")
def test_send_notification_never_raises_on_network_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("no network")
    assert send_notification("fake-token", "12345", "test") is False


@patch("src.audit_notifier.urllib.request.urlopen")
def test_send_notification_never_raises_on_http_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
    assert send_notification("fake-token", "12345", "test") is False


def test_format_signal_notification_ok_status():
    text = format_signal_notification("GOLD", "short", 4367.0, 4370.0, [4364.0, 4357.0, None], "ok")
    assert "GOLD" in text
    assert "short" in text
    assert "4367.0" in text
    assert "TP3=ouvert" in text


def test_format_signal_notification_incomplete_status():
    text = format_signal_notification("GOLD", "short", None, None, [None, None, None], "incomplete")
    assert "INCOMPLET" in text
    assert "extraction_status=incomplete" in text


def test_format_matinale_notification_contradiction():
    text = format_matinale_notification("GOLD", "haussier", "baissier", True)
    assert "Contradiction" in text
    assert "haussier" in text
    assert "baissier" in text


def test_format_matinale_notification_no_contradiction():
    text = format_matinale_notification("BTCUSD", "baissier", "baissier", False)
    assert "Contradiction" not in text
    assert "BTCUSD" in text


def test_format_matinale_notification_falls_back_to_tag_when_corps_indetermine():
    # Format réel du canal (20/08/2026) : biais_corps souvent "indetermine"
    # (texte technique sans phrase heuristique) — le tag déclaré doit
    # rester visible plutôt qu'un "indetermine" peu informatif.
    text = format_matinale_notification("BTCUSD", "indetermine", "haussier", False)
    assert "haussier" in text
    assert "indetermine" not in text


# ---------------------------------------------------------------------------
# send_document
# ---------------------------------------------------------------------------

def _fake_requests_response(ok_payload: dict, http_ok: bool = True):
    mock = MagicMock()
    mock.ok = http_ok
    mock.json.return_value = ok_payload
    return mock


@patch("requests.post")
def test_send_document_returns_true_on_telegram_ok(mock_post, tmp_path):
    file_path = tmp_path / "dashboard.html"
    file_path.write_text("<html></html>", encoding="utf-8")
    mock_post.return_value = _fake_requests_response({"ok": True})

    assert send_document("tok", "chat", str(file_path), "dashboard.html") is True
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["chat_id"] == "chat"
    assert "document" in kwargs["files"]


@patch("requests.post")
def test_send_document_returns_false_on_telegram_error_payload(mock_post, tmp_path):
    file_path = tmp_path / "dashboard.html"
    file_path.write_text("<html></html>", encoding="utf-8")
    mock_post.return_value = _fake_requests_response({"ok": False, "description": "bad chat"})
    assert send_document("tok", "chat", str(file_path), "dashboard.html") is False


@patch("requests.post")
def test_send_document_returns_false_on_http_error(mock_post, tmp_path):
    file_path = tmp_path / "dashboard.html"
    file_path.write_text("<html></html>", encoding="utf-8")
    mock_post.return_value = _fake_requests_response({}, http_ok=False)
    assert send_document("tok", "chat", str(file_path), "dashboard.html") is False


def test_send_document_returns_false_when_file_missing(tmp_path):
    assert send_document("tok", "chat", str(tmp_path / "nope.html"), "nope.html") is False


@patch("requests.post")
def test_send_document_never_raises_on_network_error(mock_post, tmp_path):
    import requests

    file_path = tmp_path / "dashboard.html"
    file_path.write_text("<html></html>", encoding="utf-8")
    mock_post.side_effect = requests.RequestException("no network")
    assert send_document("tok", "chat", str(file_path), "dashboard.html") is False
