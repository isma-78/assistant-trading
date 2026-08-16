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
