"""
capital_manager.py — Suivi de l'enveloppe de capital (démo ou réel).

Toute variation de l'enveloppe est journalisée. Aucune recharge silencieuse :
une recharge sans justification est une erreur de code, pas une option
d'exécution (voir "CE QU'IL NE FAUT PAS FAIRE" du guide de démarrage P0).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


@dataclass(frozen=True)
class EnvelopeEvent:
    timestamp: str
    kind: str  # "init" | "trade_pnl" | "reload"
    amount: float
    balance_after: float
    note: str = ""


class CapitalManager:
    def __init__(self, initial_balance: float):
        if initial_balance <= 0:
            raise ValueError("initial_balance doit être > 0")
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.history: List[EnvelopeEvent] = []
        self._log("init", initial_balance, "Enveloppe initiale")

    def _log(self, kind: str, amount: float, note: str = "") -> None:
        self.history.append(
            EnvelopeEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                kind=kind,
                amount=amount,
                balance_after=self.balance,
                note=note,
            )
        )

    def apply_trade_pnl(self, pnl: float, note: str = "") -> None:
        self.balance = round(self.balance + pnl, 2)
        self._log("trade_pnl", pnl, note)

    def reload(self, amount: float, note: str) -> None:
        """Recharge d'enveloppe — toujours explicite et journalisée."""
        if amount <= 0:
            raise ValueError("Le montant de recharge doit être positif")
        if not note:
            raise ValueError("Une recharge doit obligatoirement être justifiée (paramètre note)")
        self.balance = round(self.balance + amount, 2)
        self._log("reload", amount, note)

    def is_depleted(self) -> bool:
        return self.balance <= 0
