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

from typing import Optional

import requests


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

    def close_position(self, deal_id: str, size: Optional[float] = None) -> dict:
        """Ferme une position, totalement (size=None) ou partiellement
        (size=fraction de la taille ouverte, pour TP1/TP2 — §2.10)."""
        body = {"size": size} if size is not None else None
        return self.delete(f"/positions/{deal_id}", body=body)

    def update_position_stop(self, deal_id: str, new_stop_level: float) -> dict:
        """Déplace le stop d'une position déjà ouverte (resserrement
        uniquement — la garde contre l'élargissement est appliquée par
        l'appelant via risk_engine.evaluate_stop_update, jamais ici)."""
        return self.put(f"/positions/{deal_id}", {"stopLevel": new_stop_level})
