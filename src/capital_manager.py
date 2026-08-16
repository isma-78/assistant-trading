"""
capital_manager.py — Suivi de l'enveloppe de capital (démo ou réel).

Toute variation de l'enveloppe est journalisée. Aucune recharge silencieuse :
une recharge sans justification est une erreur de code, pas une option
d'exécution (voir "CE QU'IL NE FAUT PAS FAIRE" du guide de démarrage P0).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Tuple


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


def apply_trade_result(
    envelope: CapitalManager, pnl: float, reserve_total_before: float, note: str = ""
) -> Tuple[float, float]:
    """Règle de réinvestissement des 50% (§2.3 du CDC) : un trade GAGNANT
    voit son gain net réparti moitié dans l'enveloppe de l'actif, moitié
    dans la réserve globale (sanctuarisée définitivement, jamais
    réattribuée même après une perte ultérieure). Un trade PERDANT est
    imputé en totalité à l'enveloppe, la réserve n'est jamais touchée.

    La réserve n'est PAS un CapitalManager : elle est globale (partagée
    entre TOUS les actifs, §2.3), peut légitimement démarrer à 0€ (ce que
    l'invariant `initial_balance > 0` de CapitalManager interdit à
    dessein pour une enveloppe de trading), et n'a ni rechargement ni
    notion d'épuisement — un simple total croissant suffit, persisté via
    `reserve_ledger.reserve_totale` (voir envelope_store.py). C'est
    pourquoi cette règle est une fonction séparée plutôt qu'une méthode
    de CapitalManager.

    Retourne (montant_ajoute_a_la_reserve, nouveau_total_reserve) — le
    premier est 0.0 pour une perte ou un résultat nul."""
    if pnl > 0:
        envelope_share = round(pnl * 0.5, 2)
        reserve_share = round(pnl - envelope_share, 2)
        envelope.apply_trade_pnl(envelope_share, note)
        return reserve_share, round(reserve_total_before + reserve_share, 2)

    envelope.apply_trade_pnl(pnl, note)
    return 0.0, reserve_total_before
