"""
validator.py — Revalidation déterministe d'un signal juste avant
exécution. MODULE CRITIQUE (§4.4 : validator ✅, même exigence de
couverture que risk_engine.py — demande explicite d'Ismaël pour P2).

Dernière porte avant que risk_engine ne calcule une taille de position :
vérifie que le signal reste exploitable au moment T de l'exécution, pas
au moment où il a été extrait par parser.py (§2.8, §3.5 du CDC).

Contrôles :
- actif en liste blanche (redondant avec risk_engine par défense en
  profondeur — un signal qui n'aurait jamais dû arriver jusqu'ici est
  rejeté ici aussi, jamais laissé passer par oubli d'un des deux garde-fous)
- marché TRADEABLE au moment de la validation (pas fermé/suspendu)
- prix courant disponible et plausible (pas None, pas <= 0)
- fenêtre de péremption (§2.8) : le prix courant ne s'est pas trop
  éloigné du prix du signal — au-delà, le rapport gain/risque planifié
  n'est plus celui du signal d'origine, l'entrée n'a plus de sens

Aucun LLM ici (invariant #1) : entièrement déterministe, fail-safe
(invariant #7 — toute erreur interne devient un rejet explicite, jamais
une entrée laissée passer par défaut).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from src.risk_engine import AssetSpec

# Tolérance de péremption (§2.8) : le CDC fixe le PRINCIPE ("fenêtre de
# péremption... dans une tolérance définie") mais pas de valeur chiffrée
# — décision d'ingénieur documentée dans docs/DECISIONS.md. Choix : la
# moitié de la distance prévue jusqu'au stop. Justification : si le
# marché a déjà parcouru la moitié du risque prévu avant même l'entrée,
# le rapport gain/risque planifié n'est plus celui évalué par le canal,
# l'entrée n'a plus le sens qu'avait le signal d'origine.
STALENESS_FRACTION_OF_STOP_DISTANCE = 0.5


class ValidationRejectionReason(str, Enum):
    ASSET_NOT_WHITELISTED = "asset_not_whitelisted"
    MARKET_NOT_TRADEABLE = "market_not_tradeable"
    PRICE_UNAVAILABLE = "price_unavailable"
    SIGNAL_STALE = "signal_stale"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class ValidationResult:
    approved: bool
    reason: Optional[ValidationRejectionReason] = None
    detail: str = ""


def validate_signal(
    asset: str,
    entry_price: float,
    stop_price: float,
    current_price: Optional[float],
    market_status: str,
    whitelist: Dict[str, AssetSpec],
) -> ValidationResult:
    """Point d'entrée unique. Ne lève jamais d'exception : toute erreur
    interne devient un rejet explicite (fail-safe, invariant #7 — même
    patron que risk_engine.evaluate_new_entry)."""
    try:
        return _validate_signal(asset, entry_price, stop_price, current_price, market_status, whitelist)
    except Exception as exc:
        return ValidationResult(
            approved=False,
            reason=ValidationRejectionReason.INTERNAL_ERROR,
            detail=f"Erreur interne bloquante, entrée refusée par sécurité : {exc}",
        )


def _validate_signal(
    asset: str, entry_price: float, stop_price: float, current_price: Optional[float],
    market_status: str, whitelist: Dict[str, AssetSpec],
) -> ValidationResult:
    if asset not in whitelist:
        return ValidationResult(
            approved=False, reason=ValidationRejectionReason.ASSET_NOT_WHITELISTED,
            detail=f"{asset} absent de la liste blanche",
        )

    if market_status != "TRADEABLE":
        return ValidationResult(
            approved=False, reason=ValidationRejectionReason.MARKET_NOT_TRADEABLE,
            detail=f"Marché {asset} non tradeable (statut={market_status})",
        )

    if current_price is None or current_price <= 0:
        return ValidationResult(
            approved=False, reason=ValidationRejectionReason.PRICE_UNAVAILABLE,
            detail=f"Prix courant indisponible ou invalide pour {asset} : {current_price!r}",
        )

    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return ValidationResult(
            approved=False, reason=ValidationRejectionReason.SIGNAL_STALE,
            detail="Distance de stop nulle ou invalide, signal incohérent",
        )

    drift = abs(current_price - entry_price)
    tolerance = stop_distance * STALENESS_FRACTION_OF_STOP_DISTANCE
    if drift > tolerance:
        return ValidationResult(
            approved=False, reason=ValidationRejectionReason.SIGNAL_STALE,
            detail=(
                f"Prix courant {current_price} trop éloigné du signal {entry_price} "
                f"(écart {drift:.6g} > tolérance {tolerance:.6g} = "
                f"{STALENESS_FRACTION_OF_STOP_DISTANCE * 100:.0f}% de la distance de stop)"
            ),
        )

    return ValidationResult(approved=True, detail="Signal validé : liste blanche, marché ouvert, prix cohérent")
