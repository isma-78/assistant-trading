"""
Tests de config.py — chargement des identifiants du compte démo dédié à
l'Hypothèse #3 (20/08/2026, voir docs/DECISIONS.md) : optionnels, ne
doivent jamais faire échouer le chargement des processus existants qui
ne les utilisent pas.
"""

from src.config import load_config

_REQUIRED_ENV = {
    "TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h", "TELEGRAM_PHONE": "+33000000000",
    "TELEGRAM_CHANNEL": "@x", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1",
    "CAPITAL_API_KEY": "k", "CAPITAL_IDENTIFIER": "main@example.com", "CAPITAL_API_PASSWORD": "p",
    "EXTRACTION_API_KEY": "e", "ANTHROPIC_API_KEY": "a",
}


def _set_required_env(monkeypatch):
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_load_config_hypothesis3_fields_absent_by_default(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    config = load_config(dotenv_path=str(tmp_path / "nonexistent.env"))
    assert config.capital_api_key_hypothesis3 is None
    assert config.capital_api_password_hypothesis3 is None
    # Repli sur CAPITAL_IDENTIFIER quand CAPITAL_IDENTIFIER_HYPOTHESIS3 absent.
    assert config.capital_identifier_hypothesis3 == "main@example.com"


def test_load_config_hypothesis3_fields_loaded_when_present(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("CAPITAL_API_KEY_HYPOTHESIS3", "k3")
    monkeypatch.setenv("CAPITAL_IDENTIFIER_HYPOTHESIS3", "h3@example.com")
    monkeypatch.setenv("CAPITAL_API_PASSWORD_HYPOTHESIS3", "p3")
    config = load_config(dotenv_path=str(tmp_path / "nonexistent.env"))
    assert config.capital_api_key_hypothesis3 == "k3"
    assert config.capital_identifier_hypothesis3 == "h3@example.com"
    assert config.capital_api_password_hypothesis3 == "p3"


def test_load_config_hypothesis3_identifier_explicit_overrides_fallback(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("CAPITAL_IDENTIFIER_HYPOTHESIS3", "h3@example.com")
    config = load_config(dotenv_path=str(tmp_path / "nonexistent.env"))
    assert config.capital_identifier_hypothesis3 == "h3@example.com"
    assert config.capital_identifier == "main@example.com"  # compte principal inchangé
