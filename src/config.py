"""
config.py — Chargement et validation de la configuration.

Toute la configuration critique (plafonds de risque, environnement
Capital.com) est chargée UNE SEULE FOIS au démarrage et considérée IMMUABLE pendant
l'exécution (invariant #6 du projet : les plafonds de risque ne sont pas
modifiables à chaud, uniquement par redéploiement).

Pour changer un plafond : modifier le .env et redémarrer le processus.
Ne jamais le modifier en mémoire pendant que le programme tourne.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Levée si une variable d'environnement obligatoire est manquante ou invalide."""


@dataclass(frozen=True)
class AppConfig:
    telegram_api_id: str
    telegram_api_hash: str
    telegram_phone: str
    telegram_channel: str
    telegram_bot_token: str
    telegram_chat_id: str

    capital_api_key: str
    capital_identifier: str
    capital_api_password: str
    capital_environment: str  # "demo" | "live"

    extraction_api_key: str
    anthropic_api_key: str

    db_path: str
    confidence_threshold: float
    risk_percent_default: float
    risk_percent_boosted: float
    envelope_initial: float


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"Variable d'environnement manquante ou vide : {name}")
    return value


def load_config(dotenv_path: str = ".env") -> AppConfig:
    """
    Charge et valide la configuration depuis le fichier .env (et l'environnement
    système). Lève ConfigError si une valeur obligatoire manque ou est invalide.

    À appeler une seule fois au démarrage du processus (main.py). Le résultat
    doit être passé en paramètre aux autres modules, jamais relu depuis
    os.environ ailleurs dans le code.
    """
    load_dotenv(dotenv_path=dotenv_path, override=False)

    environment = os.environ.get("CAPITAL_ENVIRONMENT", "demo")
    if environment not in ("demo", "live"):
        raise ConfigError(
            f"CAPITAL_ENVIRONMENT invalide : {environment!r} (attendu 'demo' ou 'live')"
        )

    risk_default = float(os.environ.get("RISK_PERCENT_DEFAULT", "2"))
    risk_boosted = float(os.environ.get("RISK_PERCENT_BOOSTED", "4"))
    envelope_initial = float(os.environ.get("ENVELOPE_INITIAL", "500"))
    confidence_threshold = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.75"))

    if not (0 < risk_default <= risk_boosted):
        raise ConfigError(
            f"RISK_PERCENT_DEFAULT ({risk_default}) doit être > 0 et <= RISK_PERCENT_BOOSTED ({risk_boosted})"
        )
    if envelope_initial <= 0:
        raise ConfigError(f"ENVELOPE_INITIAL ({envelope_initial}) doit être > 0")
    if not (0 < confidence_threshold <= 1):
        raise ConfigError(f"CONFIDENCE_THRESHOLD ({confidence_threshold}) doit être entre 0 et 1")

    return AppConfig(
        telegram_api_id=_require("TELEGRAM_API_ID"),
        telegram_api_hash=_require("TELEGRAM_API_HASH"),
        telegram_phone=_require("TELEGRAM_PHONE"),
        telegram_channel=_require("TELEGRAM_CHANNEL"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        capital_api_key=_require("CAPITAL_API_KEY"),
        capital_identifier=_require("CAPITAL_IDENTIFIER"),
        capital_api_password=_require("CAPITAL_API_PASSWORD"),
        capital_environment=environment,
        extraction_api_key=_require("EXTRACTION_API_KEY"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        db_path=os.environ.get("DB_PATH", "./data/assistant_trading.db"),
        confidence_threshold=confidence_threshold,
        risk_percent_default=risk_default,
        risk_percent_boosted=risk_boosted,
        envelope_initial=envelope_initial,
    )
