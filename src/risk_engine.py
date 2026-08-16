"""
risk_engine.py — Moteur de risque déterministe. MODULE CRITIQUE.

INVARIANTS NON NÉGOCIABLES (voir CDC v4 et instructions projet) :
- Aucun LLM n'intervient ici. Tout est calcul déterministe et testé (inv. #1, #2).
- Aucun signal ne devient un ordre sans passer par evaluate_new_entry (inv. #3).
- Un stop ne peut être resserré, jamais élargi (inv. #5).
- Aucune moyenne à la baisse, aucune augmentation de position perdante (inv. #5).
- Les plafonds de risque (RiskCaps) sont figés à la construction du moteur —
  jamais modifiés à chaud, uniquement par redéploiement (inv. #6).
- Fail-safe : toute exception interne bloque l'entrée, elle ne la laisse
  jamais passer (inv. #7).
- Le score de confiance est un simple paramètre d'entrée ici : il est
  calculé statistiquement ailleurs (confidence_scorer.py), jamais jugé par
  un LLM (inv. #9).

Ce module ne touche jamais au broker. Il produit des décisions
(RiskDecision) que l'executor applique ou rejette telles quelles.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class RiskRejectionReason(str, Enum):
    GO_NOGO_LOCKED = "go_nogo_locked"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    ASSET_NOT_WHITELISTED = "asset_not_whitelisted"
    ASSET_CLOSED_WEEKEND = "asset_closed_weekend"
    STOP_MISSING = "stop_missing"
    STOP_INVALID_SIDE = "stop_invalid_side"
    STOP_WIDENED = "stop_widened"
    AVERAGING_DOWN = "averaging_down"
    ENVELOPE_DEPLETED = "envelope_depleted"
    POSITION_SIZE_BELOW_MINIMUM = "position_size_below_minimum"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class RiskCaps:
    """
    Plafonds de risque. Chargés une fois depuis la config au démarrage du
    RiskEngine et jamais modifiés en mémoire (invariant #6).
    """
    risk_percent_default: float   # ex: 2.0
    risk_percent_boosted: float   # ex: 4.0
    envelope_initial: float       # ex: 500.0

    def __post_init__(self):
        if not (0 < self.risk_percent_default <= self.risk_percent_boosted):
            raise ValueError(
                "risk_percent_default doit être > 0 et <= risk_percent_boosted"
            )
        if self.envelope_initial <= 0:
            raise ValueError("envelope_initial doit être > 0")


@dataclass(frozen=True)
class AssetSpec:
    """
    Spécification d'un actif tradable. Dépend directement du tableau des
    tailles minimales §1.2 du CDC — à finaliser avant toute exécution réelle.
    """
    symbol: str
    min_units: float
    pip_value_per_unit: float  # valeur d'1 unité de mouvement de prix, en devise du compte
    weekend_tradable: bool = False

    def __post_init__(self):
        if self.min_units <= 0:
            raise ValueError(f"min_units doit être > 0 pour {self.symbol}")
        if self.pip_value_per_unit <= 0:
            raise ValueError(f"pip_value_per_unit doit être > 0 pour {self.symbol}")


@dataclass(frozen=True)
class TradeSignal:
    """
    Signal déjà classifié/extrait/validé en amont (message_classifier +
    parser + validator). Le risk_engine ne réinterprète jamais le texte
    d'origine, il ne fait que valider les chiffres.
    """
    asset: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_price: Optional[float]
    confidence: float  # score statistique [0, 1], calculé ailleurs
    boosted: bool = False


@dataclass(frozen=True)
class ExistingPosition:
    asset: str
    direction: str
    entry_price: float
    stop_price: float
    is_losing: bool


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    units: float = 0.0
    risk_amount_eur: float = 0.0
    reason: Optional[RiskRejectionReason] = None
    detail: str = ""


class RiskEngine:
    def __init__(self, caps: RiskCaps, whitelist: Dict[str, AssetSpec]):
        self.caps = caps
        self.whitelist = dict(whitelist)  # copie défensive

    def evaluate_new_entry(
        self,
        signal: TradeSignal,
        envelope_balance: float,
        confidence_threshold: float,
        go_nogo_ok: bool,
        existing_position: Optional[ExistingPosition] = None,
        is_weekend: bool = False,
    ) -> RiskDecision:
        """
        Point d'entrée unique pour valider un signal avant exécution.
        Ne lève jamais d'exception : toute erreur interne devient un rejet
        explicite (fail-safe, invariant #7).
        """
        try:
            return self._evaluate_new_entry(
                signal, envelope_balance, confidence_threshold,
                go_nogo_ok, existing_position, is_weekend,
            )
        except Exception as exc:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.INTERNAL_ERROR,
                detail=f"Erreur interne bloquante, entrée refusée par sécurité : {exc}",
            )

    def _evaluate_new_entry(
        self,
        signal: TradeSignal,
        envelope_balance: float,
        confidence_threshold: float,
        go_nogo_ok: bool,
        existing_position: Optional[ExistingPosition],
        is_weekend: bool,
    ) -> RiskDecision:
        if not go_nogo_ok:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.GO_NOGO_LOCKED,
                detail="Verrou Go/No-Go non validé pour cet actif/source.",
            )

        if signal.confidence < confidence_threshold:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.CONFIDENCE_BELOW_THRESHOLD,
                detail=f"Confiance {signal.confidence:.2f} < seuil {confidence_threshold:.2f}",
            )

        asset_spec = self.whitelist.get(signal.asset)
        if asset_spec is None:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.ASSET_NOT_WHITELISTED,
                detail=f"{signal.asset} absent de la liste blanche",
            )

        if is_weekend and not asset_spec.weekend_tradable:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.ASSET_CLOSED_WEEKEND,
                detail=f"{signal.asset} fermé le week-end",
            )

        if signal.stop_price is None:
            return RiskDecision(approved=False, reason=RiskRejectionReason.STOP_MISSING)

        if signal.direction == "long" and signal.stop_price >= signal.entry_price:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.STOP_INVALID_SIDE,
                detail="Stop au-dessus ou égal à l'entrée pour un long",
            )
        if signal.direction == "short" and signal.stop_price <= signal.entry_price:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.STOP_INVALID_SIDE,
                detail="Stop en-dessous ou égal à l'entrée pour un short",
            )

        # Invariant #5 : aucune moyenne à la baisse, aucun ajout sur position perdante
        if (
            existing_position is not None
            and existing_position.asset == signal.asset
            and existing_position.is_losing
        ):
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.AVERAGING_DOWN,
                detail="Position existante perdante sur cet actif : ajout interdit",
            )

        if envelope_balance <= 0:
            return RiskDecision(approved=False, reason=RiskRejectionReason.ENVELOPE_DEPLETED)

        risk_pct = self.caps.risk_percent_boosted if signal.boosted else self.caps.risk_percent_default
        risk_amount_eur = envelope_balance * (risk_pct / 100.0)

        # stop_distance > 0 garanti par les deux contrôles ci-dessus (stop du bon côté)
        stop_distance = abs(signal.entry_price - signal.stop_price)

        raw_units = risk_amount_eur / (stop_distance * asset_spec.pip_value_per_unit)
        units = self._round_down_to_min(raw_units, asset_spec.min_units)

        if units < asset_spec.min_units:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.POSITION_SIZE_BELOW_MINIMUM,
                detail=f"Taille calculée {units} < minimum {asset_spec.min_units}",
            )

        actual_risk = units * stop_distance * asset_spec.pip_value_per_unit
        return RiskDecision(approved=True, units=units, risk_amount_eur=round(actual_risk, 2))

    @staticmethod
    def _round_down_to_min(raw_units: float, min_units: float) -> float:
        """Arrondi à l'inférieur au multiple de min_units. Garantit que le
        risque réel ne dépasse jamais le risque autorisé calculé."""
        if raw_units <= 0:
            return 0.0
        steps = int(raw_units / min_units)
        return round(steps * min_units, 8)

    def evaluate_stop_update(
        self, current_stop: float, new_stop: float, direction: str
    ) -> RiskDecision:
        """Un stop ne peut être que resserré (invariant #5). Ne lève jamais
        d'exception (fail-safe)."""
        try:
            if direction == "long":
                widened = new_stop < current_stop
            elif direction == "short":
                widened = new_stop > current_stop
            else:
                return RiskDecision(
                    approved=False,
                    reason=RiskRejectionReason.STOP_INVALID_SIDE,
                    detail=f"Direction inconnue : {direction}",
                )

            if widened:
                return RiskDecision(
                    approved=False,
                    reason=RiskRejectionReason.STOP_WIDENED,
                    detail=f"{current_stop} -> {new_stop} élargit le stop, refusé",
                )
            return RiskDecision(approved=True, detail="Resserrement de stop validé")
        except Exception as exc:
            return RiskDecision(
                approved=False,
                reason=RiskRejectionReason.INTERNAL_ERROR,
                detail=f"Erreur interne bloquante, mise à jour refusée par sécurité : {exc}",
            )
