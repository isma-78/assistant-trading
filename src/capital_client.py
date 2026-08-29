"""
capital_client.py — Client HTTP pour l'API Capital.com, factorisant la
logique déjà dupliquée dans trois scripts existants
(test_capital_connection.py, discover_instruments.py,
calibrate_pip_value.py) : session, GET/POST/DELETE, gestion des tokens
CST/X-SECURITY-TOKEN, et la particularité de l'API découverte pendant le
palier P0 — le `dealId` de premier niveau de `/confirms/{ref}` est l'ID
de l'ORDRE, pas de la POSITION (le vrai `dealId` est dans
`affectedDeals[0].dealId`). Voir docs/DECISIONS.md.

Aucune décision ici : ce module transporte des requêtes HTTP vers le
broker, il ne décide jamais quoi trader ni combien (invariant #1). Les
appelants (`market_data.py`, `executor.py`) restent seuls responsables de
toute logique de décision.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# close_position() : nombre de tentatives de résolution de la confirmation
# de clôture et délai entre elles (28/08/2026, voir docs/DECISIONS.md et
# la docstring de close_position — confirmation périmée d'ouverture
# observée sur 2 clôtures réelles sur 3, corrigée en retentant jusqu'à
# voir status="CLOSED"). Valeurs arbitraires mais généreuses : le coût
# d'attendre jusqu'à 3s de plus après une position déjà fermée côté
# broker est nul, contrairement au coût d'une donnée fausse persistée.
_CLOSE_CONFIRM_MAX_ATTEMPTS = 4
_CLOSE_CONFIRM_RETRY_DELAY_SECONDS = 1.0


def _parse_broker_datetime(value: str) -> datetime:
    """Parse un horodatage ISO 8601, broker (souvent sans fuseau — traité
    comme UTC implicite, cohérent avec le reste de l'API Capital.com) ou
    interne (`executor._now()`, toujours avec fuseau explicite). Les deux
    formats doivent être comparables entre eux (28/08/2026, voir
    docs/DECISIONS.md — second discriminant de `close_position`)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class CapitalApiError(RuntimeError):
    """Erreur de communication avec l'API Capital.com (HTTP, session non
    ouverte, réponse inattendue)."""


class CapitalClient:
    def __init__(self, api_key: str, identifier: str, password: str, base_url: str, session=None):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._tokens: Optional[dict] = None

    def login(self) -> dict:
        resp = self._session.post(
            f"{self.base_url}/session",
            headers={"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"identifier": self.identifier, "password": self.password, "encryptedPassword": False},
            timeout=10,
        )
        self._raise_for_status(resp)
        try:
            self._tokens = {"CST": resp.headers["CST"], "X-SECURITY-TOKEN": resp.headers["X-SECURITY-TOKEN"]}
        except KeyError as exc:
            raise CapitalApiError(f"Réponse de session sans tokens CST/X-SECURITY-TOKEN : {resp.headers}") from exc
        return dict(self._tokens)

    def switch_account(self, account_id: str) -> dict:
        """Cible EXPLICITEMENT le compte actif (`PUT /session`,
        `accountId`) — à appeler juste après `login()`, avant tout autre
        appel qui dépendrait du compte actif (ordres, positions, solde).

        Incident réel du 20/08/2026 (voir docs/DECISIONS.md) : le compte
        "préféré" par défaut d'un identifiant Capital.com est un état
        PARTAGÉ entre toutes les clés API de cet identifiant — créer un
        nouveau compte démo sur la plateforme peut faire basculer
        silencieusement ce flag vers un compte différent, y compris vide,
        sans qu'aucun code n'en soit informé. Ne jamais dépendre du
        compte "préféré" pour une session utilisée en production :
        toujours cibler explicitement par `accountId`.

        Capture les nouveaux tokens CST/X-SECURITY-TOKEN si la réponse en
        renvoie (comportement non garanti par la documentation publique) ;
        sinon conserve ceux déjà obtenus par `login()`, qui restent
        valides pour le compte nouvellement actif dans ce cas.

        Incident réel du 21/08/2026 (voir docs/DECISIONS.md) : si le
        compte "préféré" (partagé, voir ci-dessus) coïncide DÉJÀ avec
        `account_id` au moment de `login()` — plausible pour n'importe
        laquelle des hypothèses, puisque ce flag change silencieusement
        selon quelle clé API a été utilisée en dernier sur la plateforme
        — Capital.com rejette le `PUT /session` avec
        `error.not-different.accountId`, un 400 traité comme une erreur
        fatale par le code appelant (process planté au démarrage,
        `hypothesis2_executor` le premier jour de son lancement). Ce
        n'est pourtant pas une erreur : la post-condition voulue (la
        session cible bien `account_id`) est déjà remplie. Vérifié via
        `GET /accounts` AVANT de tenter le `PUT` plutôt que d'intercepter
        le message d'erreur (même principe que la réconciliation de
        `executor._apply_management_action`, 21/08/2026 : une lecture
        fraîche de l'état réel, jamais un texte d'erreur qui pourrait
        changer de format) — si `account_id` est déjà le compte actif
        (`preferred: true`), aucun appel PUT n'est tenté."""
        accounts = self.get("/accounts").get("accounts", [])
        already_active = any(
            acc.get("accountId") == account_id and acc.get("preferred") for acc in accounts
        )
        if already_active:
            return {"accountId": account_id, "alreadyActive": True}

        resp = self._session.put(
            f"{self.base_url}/session",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"accountId": account_id},
            timeout=10,
        )
        self._raise_for_status(resp)
        if "CST" in resp.headers and "X-SECURITY-TOKEN" in resp.headers:
            self._tokens = {"CST": resp.headers["CST"], "X-SECURITY-TOKEN": resp.headers["X-SECURITY-TOKEN"]}
        return resp.json()

    def _headers(self) -> dict:
        if self._tokens is None:
            raise CapitalApiError("login() doit être appelé avant toute requête")
        return {"X-CAP-API-KEY": self.api_key, **self._tokens}

    @staticmethod
    def _raise_for_status(resp) -> None:
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            # str(exc) seul n'inclut jamais le corps de la réponse —
            # c'est pourtant là que Capital.com place errorCode (ex:
            # "error.vallidation.guaranteed-stop-loss.required"), le
            # seul indice exploitable pour diagnostiquer un rejet.
            # Bug réel trouvé le 16/08/2026 : un échec silencieux de ce
            # détail a fait perdre du temps de diagnostic pendant le
            # test réel encadré d'executor.py (voir docs/DECISIONS.md).
            raise CapitalApiError(f"{exc} — corps de la réponse : {resp.text}") from exc

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = self._session.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    def post(self, path: str, body: dict) -> dict:
        resp = self._session.post(
            f"{self.base_url}{path}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body, timeout=10,
        )
        self._raise_for_status(resp)
        return resp.json()

    def put(self, path: str, body: dict) -> dict:
        resp = self._session.put(
            f"{self.base_url}{path}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body, timeout=10,
        )
        self._raise_for_status(resp)
        return resp.json()

    def delete(self, path: str, body: Optional[dict] = None) -> dict:
        resp = self._session.delete(
            f"{self.base_url}{path}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body, timeout=10,
        )
        self._raise_for_status(resp)
        return resp.json()

    # --- Opérations métier de haut niveau, partagées entre market_data.py et executor.py ---

    def get_market_snapshot(self, epic: str) -> dict:
        return self.get(f"/markets/{epic}")

    def get_prices(self, epic: str, resolution: str = "HOUR", max_bars: int = 50) -> dict:
        return self.get(f"/prices/{epic}", params={"resolution": resolution, "max": max_bars})

    def get_account_balance(self) -> tuple:
        accounts = self.get("/accounts")
        for acc in accounts.get("accounts", []):
            if acc.get("preferred"):
                return acc.get("balance", {}).get("balance"), acc.get("currency")
        raise CapitalApiError("Aucun compte 'preferred' trouvé dans la réponse /accounts")

    def get_open_positions(self) -> list:
        return self.get("/positions").get("positions", [])

    def _submit_and_confirm(self, path: str, body: dict) -> dict:
        """Poste un ordre (position marché ou ordre limite) et attend sa
        confirmation. Lève CapitalApiError si refusé ou incomplet —
        jamais un deal_id invalide en silence (fail-safe, invariant #7 :
        à l'appelant de traiter l'exception comme un rejet, jamais comme
        un ordre placé par défaut). Le `dealId` de premier niveau de
        `/confirms/{ref}` est celui de l'ORDRE, pas de la POSITION/du
        WORKING ORDER (constaté au palier P0 sur /positions, confirmé
        aussi sur /workingorders au palier P2) — le vrai `dealId` est
        dans `affectedDeals[0].dealId`."""
        result = self.post(path, body)
        deal_reference = result.get("dealReference")
        if not deal_reference:
            raise CapitalApiError(f"Pas de dealReference retourné par {path} : {result}")

        confirmation = self.get(f"/confirms/{deal_reference}")
        if confirmation.get("dealStatus") != "ACCEPTED":
            raise CapitalApiError(f"Ordre refusé par le broker ({path}) : {confirmation}")

        affected = confirmation.get("affectedDeals") or []
        deal_id = affected[0]["dealId"] if affected else None
        if deal_id is None:
            raise CapitalApiError(f"Pas de dealId dans affectedDeals de la confirmation : {confirmation}")

        return {"deal_id": deal_id, "level": confirmation.get("level"), "confirmation": confirmation}

    def open_position(
        self, epic: str, direction: str, size: float,
        guaranteed_stop: bool = False, stop_distance: Optional[float] = None,
    ) -> dict:
        """Ouvre une position AU MARCHÉ (exécution immédiate). Réservé
        aux besoins ponctuels (ex: calibrate_pip_value.py) — l'exécuteur
        de trading (executor.py) utilise place_limit_order(), jamais
        celle-ci : le CDC (§2.8) interdit les ordres au marché pour
        l'exécution des signaux ("Ordres limite dans la zone d'entrée,
        jamais au marché")."""
        body = {"epic": epic, "direction": direction, "size": size}
        if guaranteed_stop:
            body["guaranteedStop"] = True
            body["stopDistance"] = stop_distance
        return self._submit_and_confirm("/positions", body)

    def place_limit_order(
        self, epic: str, direction: str, size: float, level: float,
        guaranteed_stop: bool = False, stop_distance: Optional[float] = None,
    ) -> dict:
        """Place un ordre limite (§2.8 : jamais d'ordre au marché pour
        l'exécution d'un signal) au niveau `level`, en attente
        (GOOD_TILL_CANCELLED côté Capital.com par défaut) jusqu'à
        exécution ou annulation explicite (voir cancel_working_order —
        utilisé par executor.py pour la fenêtre de péremption §2.8)."""
        body = {"epic": epic, "direction": direction, "size": size, "level": level, "type": "LIMIT"}
        if guaranteed_stop:
            body["guaranteedStop"] = True
            body["stopDistance"] = stop_distance
        return self._submit_and_confirm("/workingorders", body)

    def cancel_working_order(self, deal_id: str) -> dict:
        """Annule un ordre limite non encore exécuté (péremption §2.8,
        ou signal invalidé entre temps)."""
        return self.delete(f"/workingorders/{deal_id}")

    def get_working_orders(self) -> list:
        return self.get("/workingorders").get("workingOrders", [])

    def close_position(self, deal_id: str, size: Optional[float] = None, requested_at: Optional[str] = None) -> dict:
        """Ferme une position, totalement (size=None) ou partiellement
        (size=fraction de la taille ouverte, pour TP1/TP2 — §2.10).

        `requested_at` (ISO 8601, optionnel — 28/08/2026, voir
        docs/DECISIONS.md) : second discriminant, ORTHOGONAL à
        `status="CLOSED"` ci-dessous. Si fourni, une confirmation n'est
        acceptée que si sa `date` est POSTÉRIEURE à `requested_at` — un
        chemin qui vient de prouver qu'il produit du plausible-mais-faux
        (`status="OPEN"` périmé) mérite deux vérifications indépendantes,
        pas une seule. Sans objet si omis (compatibilité arrière,
        aucun appelant existant cassé).

        Résout la confirmation (27/08/2026, voir docs/DECISIONS.md) —
        même mécanisme que `_submit_and_confirm` pour l'ouverture (POST/
        DELETE puis `GET /confirms/{dealReference}`). Best-effort,
        fail-safe (invariant #7) : la position est déjà fermée côté
        broker à ce stade, un échec de résolution de la confirmation ne
        doit jamais faire remonter d'exception ici — seul le prix réel
        reste alors non capturé pour cet appel.

        Retourne {"level": <prix réel ou None>, "executed_at": <date de
        la confirmation ou None>, "confirmation": <réponse complète ou
        None>}.

        **Confirmation périmée, confirmé empiriquement le 28/08/2026
        (voir docs/DECISIONS.md)** : `dealReference` pour une clôture
        vaut littéralement `"p_" + deal_id` (jamais un identifiant de
        TRANSACTION propre) — un `GET /confirms/{ref}` immédiat après le
        DELETE peut renvoyer la confirmation PÉRIMÉE de l'OUVERTURE
        d'origine (même niveau, même horodatage à quelques secondes de
        l'ouverture, `status="OPEN"`) plutôt que celle de la clôture qui
        vient d'avoir lieu — observé sur 2 clôtures réelles sur 3 le
        28/08/2026. Signal fiable pour distinguer les deux, vérifié sur
        ces 3 cas : une confirmation de clôture RÉELLE porte
        `status="CLOSED"` (`affectedDeals[0].status="FULLY_CLOSED"`,
        même pour une clôture partielle) ; une confirmation PÉRIMÉE
        d'ouverture porte `status="OPEN"`. Ne fait donc confiance qu'à
        `status="CLOSED"` — retente sinon (la confirmation fraîche finit
        par apparaître, latence de propagation probable côté broker),
        jamais plus de `_CLOSE_CONFIRM_MAX_ATTEMPTS` fois. Toujours
        best-effort : épuiser les tentatives sans jamais voir `"CLOSED"`
        retourne `level`/`executed_at` à `None` plutôt qu'une valeur
        périmée — une donnée fausse serait pire qu'une case vide."""
        body = {"size": size} if size is not None else None
        result = self.delete(f"/positions/{deal_id}", body=body)
        deal_reference = result.get("dealReference")
        logger.info(
            "close_position(deal_id=%s, size=%s) -> DELETE result=%s",
            deal_id, size, result,
        )
        if not deal_reference:
            return {"level": None, "executed_at": None, "confirmation": None}

        confirmation = None
        for attempt in range(_CLOSE_CONFIRM_MAX_ATTEMPTS):
            try:
                confirmation = self.get(f"/confirms/{deal_reference}")
            except CapitalApiError:
                logger.exception(
                    "close_position(deal_id=%s) : échec de la résolution de /confirms/%s",
                    deal_id, deal_reference,
                )
                return {"level": None, "executed_at": None, "confirmation": None}
            logger.info(
                "close_position(deal_id=%s) -> GET /confirms/%s (tentative %d/%d) = %s",
                deal_id, deal_reference, attempt + 1, _CLOSE_CONFIRM_MAX_ATTEMPTS, confirmation,
            )
            if confirmation.get("status") == "CLOSED" and self._is_confirmation_fresh(confirmation, requested_at):
                return {
                    "level": confirmation.get("level"),
                    "executed_at": confirmation.get("date"),
                    "confirmation": confirmation,
                }
            if attempt < _CLOSE_CONFIRM_MAX_ATTEMPTS - 1:
                time.sleep(_CLOSE_CONFIRM_RETRY_DELAY_SECONDS)

        logger.warning(
            "close_position(deal_id=%s) : confirmation jamais 'CLOSED' fraîche après %d tentatives "
            "(dernier status=%s, dernière date=%s, requested_at=%s) — prix réel non capturé pour cette jambe",
            deal_id, _CLOSE_CONFIRM_MAX_ATTEMPTS,
            confirmation.get("status") if confirmation else None,
            confirmation.get("date") if confirmation else None,
            requested_at,
        )
        return {"level": None, "executed_at": None, "confirmation": confirmation}

    @staticmethod
    def _is_confirmation_fresh(confirmation: dict, requested_at: Optional[str]) -> bool:
        """Second discriminant (28/08/2026) : sans `requested_at`,
        toujours fraîche (compatibilité arrière). Avec, exige
        `confirmation["date"] > requested_at` — une confirmation
        antérieure à la DEMANDE de clôture ne peut, par construction,
        décrire cette clôture. Fail-safe : une date manquante ou
        illisible est traitée comme PAS fraîche (jamais l'inverse — le
        doute profite à "pas de donnée", jamais à "donnée acceptée")."""
        if requested_at is None:
            return True
        raw_date = confirmation.get("date")
        if not raw_date:
            return False
        try:
            return _parse_broker_datetime(raw_date) > _parse_broker_datetime(requested_at)
        except ValueError:
            return False

    _STOPLOSS_BOUNDARY_RE = re.compile(r"error\.invalid\.stoploss\.(minvalue|maxvalue):\s*([0-9.]+)")

    def update_position_stop(
        self, deal_id: str, new_stop_level: float, guaranteed_stop: bool = False,
        direction: Optional[str] = None, current_stop_level: Optional[float] = None,
    ) -> dict:
        """Déplace le stop d'une position déjà ouverte (resserrement
        uniquement — la garde contre l'élargissement est appliquée par
        l'appelant via risk_engine.evaluate_stop_update, jamais ici).

        `guaranteed_stop` : DOIT valoir True si la position a été ouverte
        avec un stop garanti (`guaranteedStop`/`stopDistance` passés à
        place_limit_order/open_position) — sinon Capital.com rejette la
        mise à jour avec `error.vallidation.guaranteed-stop-loss.required`
        (bug réel trouvé en production le 20/08/2026 sur les 3 positions
        Flux B alors ouvertes, EURUSD/GBPUSD/US30 — voir docs/DECISIONS.md).
        Vérifié empiriquement sur le compte démo : `stopLevel` +
        `guaranteedStop: true` suffit, pas besoin de `stopDistance` pour
        une mise à jour (contrairement à l'ouverture).

        **Retry adaptatif (29/08/2026, voir docs/DECISIONS.md, refonte
        H1-H5 point E)** : 5563 échecs `error.invalid.stoploss.
        (minvalue|maxvalue)` mesurés en production sur cette opération
        précise — le seuil broker est une BANDE DYNAMIQUE (ni
        `minStepDistance` ni `%StopOrProfitDistance` ne l'expliquent,
        voir docs/DECISIONS.md 28/08/2026), jamais une constante
        pré-validable. Le message d'erreur du broker DIVULGUE la valeur
        limite exacte au moment du rejet — un seul réessai (jamais une
        boucle) avec CETTE valeur exacte, jamais une valeur devinée ou
        une formule locale. `direction`/`current_stop_level` optionnels :
        si fournis, le réessai est refusé (exception propagée telle
        quelle, aucun réessai tenté) s'il élargirait le stop actuel
        (invariant #5, jamais contourné même par ce garde-fou) ; si
        absents (rétro-compatibilité, comportement inchangé pour tout
        appelant existant), le réessai utilise la valeur divulguée sans
        cette vérification supplémentaire."""
        try:
            return self._put_stop(deal_id, new_stop_level, guaranteed_stop)
        except CapitalApiError as exc:
            match = self._STOPLOSS_BOUNDARY_RE.search(str(exc))
            if match is None:
                raise
            boundary = float(match.group(2))
            if current_stop_level is not None and direction is not None:
                if direction == "long" and boundary < current_stop_level:
                    raise
                if direction == "short" and boundary > current_stop_level:
                    raise
            logger.warning(
                "Stop rejeté par le broker pour la position %s (%s) — réessai adapté avec la valeur divulguée %s",
                deal_id, match.group(0), boundary,
            )
            return self._put_stop(deal_id, boundary, guaranteed_stop)

    def _put_stop(self, deal_id: str, level: float, guaranteed_stop: bool) -> dict:
        body = {"stopLevel": level}
        if guaranteed_stop:
            body["guaranteedStop"] = True
        return self.put(f"/positions/{deal_id}", body)
