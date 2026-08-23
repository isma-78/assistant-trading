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
from typing import Optional

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

    # Compte démo dédié à l'Hypothèse #3 (M5/M15, en préparation
    # 20/08/2026 — voir docs/DECISIONS.md), distinct du compte principal
    # Station X/H1 : clé/mot de passe API propres, jamais partagés.
    # Optionnels (Optional[str], pas _require()), donc forcément après
    # tous les champs obligatoires ci-dessus (contrainte des dataclass
    # Python) : aucun process existant n'en a besoin tant que
    # l'exécuteur H3 n'existe pas, ne doit jamais faire échouer le
    # démarrage de telegram_listener/executor_loop/trend_executor/
    # control_bot. Phase 1 (proposition docs/HYPOTHESES.md) non encore
    # validée par Ismaël : ces identifiants ne sont câblés dans AUCUNE
    # boucle d'exécution à ce stade, uniquement préparés pour un test de
    # connexion en lecture seule.
    capital_api_key_hypothesis3: Optional[str] = None
    capital_identifier_hypothesis3: Optional[str] = None
    capital_api_password_hypothesis3: Optional[str] = None

    # Ciblage explicite de compte (PUT /session, CapitalClient.
    # switch_account) — incident réel du 20/08/2026 (voir docs/
    # DECISIONS.md) : le compte "préféré" par défaut d'un identifiant
    # Capital.com est un état PARTAGÉ entre toutes les clés API de cet
    # identifiant, qui a basculé silencieusement dès la création d'un
    # nouveau compte démo sur la plateforme. Optionnels ici pour la même
    # raison que les champs H3 ci-dessus (aucun process n'a besoin de
    # tous) — mais run_executor_loop/run_trend_loop les exigent
    # explicitement à LEUR démarrage (fail-safe local, pas un blocage
    # global qui casserait telegram_listener/control_bot).
    capital_account_id: Optional[str] = None
    capital_account_id_hypothesis3: Optional[str] = None

    # Compte démo dédié à l'Hypothèse #2 (ICT/Smart Money, Option B,
    # validée par Ismaël le 21/08/2026 — voir docs/HYPOTHESES.md).
    # **Aucun identifiant dédié fourni à ce jour** (contrairement à H3) —
    # voir docs/DECISIONS.md du 21/08/2026 pour le détail et les options
    # possibles. `hypothesis2_executor.run_hypothesis2_loop` échoue net
    # (ConfigError) tant qu'ils restent absents — jamais un repli
    # silencieux vers un autre jeu d'identifiants.
    capital_api_key_hypothesis2: Optional[str] = None
    capital_identifier_hypothesis2: Optional[str] = None
    capital_api_password_hypothesis2: Optional[str] = None
    capital_account_id_hypothesis2: Optional[str] = None

    # Compte démo dédié à l'Hypothèse #4 (retour à la moyenne, Bandes de
    # Bollinger, validée en démo par Ismaël le 21/08/2026 — voir
    # docs/HYPOTHESES.md). **Aucun identifiant dédié fourni à ce jour** —
    # Ismaël les fournira directement (jamais dans la conversation, même
    # principe que H2/H3). `hypothesis4_executor.run_hypothesis4_loop`
    # échoue net (ConfigError) tant qu'ils restent absents.
    capital_api_key_hypothesis4: Optional[str] = None
    capital_identifier_hypothesis4: Optional[str] = None
    capital_api_password_hypothesis4: Optional[str] = None
    capital_account_id_hypothesis4: Optional[str] = None

    # Compte démo dédié à l'Hypothèse #5 (confluence ICT + momentum RSI,
    # régime structurel, sortie progressive §2.10 — voir
    # docs/HYPOTHESES.md, redéfinie et déployée le 23/08/2026).
    # Identifiants fournis par Ismaël directement dans `.env` (jamais
    # dans la conversation) — voir docs/DECISIONS.md. `hypothesis5_
    # executor.run_hypothesis5_loop` échoue toujours net (ConfigError)
    # si l'un d'eux venait à manquer.
    capital_api_key_hypothesis5: Optional[str] = None
    capital_identifier_hypothesis5: Optional[str] = None
    capital_api_password_hypothesis5: Optional[str] = None
    capital_account_id_hypothesis5: Optional[str] = None


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
        capital_api_key_hypothesis3=os.environ.get("CAPITAL_API_KEY_HYPOTHESIS3") or None,
        # Compte H3 potentiellement sous le même identifiant de connexion
        # que le compte principal (juste un compte démo distinct) — repli
        # sur CAPITAL_IDENTIFIER si CAPITAL_IDENTIFIER_HYPOTHESIS3 est
        # absent, jamais l'inverse.
        capital_identifier_hypothesis3=os.environ.get("CAPITAL_IDENTIFIER_HYPOTHESIS3") or os.environ.get("CAPITAL_IDENTIFIER") or None,
        capital_api_password_hypothesis3=os.environ.get("CAPITAL_API_PASSWORD_HYPOTHESIS3") or None,
        capital_account_id=os.environ.get("CAPITAL_ACCOUNT_ID") or None,
        capital_account_id_hypothesis3=os.environ.get("CAPITAL_ACCOUNT_ID_HYPOTHESIS3") or None,
        capital_api_key_hypothesis2=os.environ.get("CAPITAL_API_KEY_HYPOTHESIS2") or None,
        capital_identifier_hypothesis2=os.environ.get("CAPITAL_IDENTIFIER_HYPOTHESIS2") or os.environ.get("CAPITAL_IDENTIFIER") or None,
        capital_api_password_hypothesis2=os.environ.get("CAPITAL_API_PASSWORD_HYPOTHESIS2") or None,
        capital_account_id_hypothesis2=os.environ.get("CAPITAL_ACCOUNT_ID_HYPOTHESIS2") or None,
        capital_api_key_hypothesis4=os.environ.get("CAPITAL_API_KEY_HYPOTHESIS4") or None,
        capital_identifier_hypothesis4=os.environ.get("CAPITAL_IDENTIFIER_HYPOTHESIS4") or os.environ.get("CAPITAL_IDENTIFIER") or None,
        capital_api_password_hypothesis4=os.environ.get("CAPITAL_API_PASSWORD_HYPOTHESIS4") or None,
        capital_account_id_hypothesis4=os.environ.get("CAPITAL_ACCOUNT_ID_HYPOTHESIS4") or None,
        capital_api_key_hypothesis5=os.environ.get("CAPITAL_API_KEY_HYPOTHESIS5") or None,
        capital_identifier_hypothesis5=os.environ.get("CAPITAL_IDENTIFIER_HYPOTHESIS5") or os.environ.get("CAPITAL_IDENTIFIER") or None,
        capital_api_password_hypothesis5=os.environ.get("CAPITAL_API_PASSWORD_HYPOTHESIS5") or None,
        capital_account_id_hypothesis5=os.environ.get("CAPITAL_ACCOUNT_ID_HYPOTHESIS5") or None,
    )
