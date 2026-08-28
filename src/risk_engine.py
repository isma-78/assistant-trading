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
from typing import Dict, List, Optional, Tuple


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
    POSITION_SIZE_STEP_DEVIATION = "position_size_step_deviation"
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
    # `size_step` (28/08/2026, voir docs/DECISIONS.md, point 3) : pas de
    # taille RÉEL Capital.com (`minStepDistance`, vérifié en direct via
    # GET /markets/{epic}) — DISTINCT de `min_units` (`minDealSize`) sur
    # 4/8 actifs de la liste blanche (US100/US30 : ×100 ; BTCUSD : ×500 ;
    # ETHUSD : ×10). None = pas encore vérifié pour cet actif, jamais
    # traité comme "aucun écart" (voir evaluate_sizing_plausibility).
    size_step: Optional[float] = None

    def __post_init__(self):
        if self.min_units <= 0:
            raise ValueError(f"min_units doit être > 0 pour {self.symbol}")
        if self.pip_value_per_unit <= 0:
            raise ValueError(f"pip_value_per_unit doit être > 0 pour {self.symbol}")
        if self.size_step is not None and self.size_step <= 0:
            raise ValueError(f"size_step doit être > 0 si fourni pour {self.symbol}")


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


# Écart maximal toléré (28/08/2026, voir docs/DECISIONS.md, point 3)
# entre le risque cible et le risque réel une fois la taille arrondie au
# VRAI pas du broker (`AssetSpec.size_step`) — au-delà, le backtest
# mesurerait une stratégie non exécutable sur cet actif à ce risque.
MAX_SIZE_STEP_RISK_DEVIATION = 0.20


def evaluate_sizing_plausibility(
    units: float,
    target_risk_eur: float,
    stop_distance: float,
    pip_value_per_unit: float,
    size_step: Optional[float],
    max_deviation: float = MAX_SIZE_STEP_RISK_DEVIATION,
) -> Tuple[bool, str]:
    """Pure, 100% couverte. Vérifie que le risque RÉEL, une fois `units`
    (déjà arrondi à `min_units`) re-arrondi au PAS RÉEL du broker
    (`size_step` — distinct de `min_units` sur plusieurs actifs, voir
    docs/DECISIONS.md 28/08/2026), ne dévie pas de plus de
    `max_deviation` de `target_risk_eur`.

    Toujours plausible (`True, ""`) si `size_step` est `None` ou <= 0 —
    fail-safe : un actif pas encore vérifié n'est JAMAIS rejeté faute de
    donnée (même convention que `causal_decomposition.
    is_cout_sortie_plausible`). Ne modifie jamais `units` — signale
    seulement, ne recalcule jamais le sizing à la hausse (invariant #2)."""
    if size_step is None or size_step <= 0:
        return True, ""
    steps = int(units / size_step)
    step_rounded_units = round(steps * size_step, 10)
    if step_rounded_units <= 0:
        return False, (
            f"Taille arrondie au pas réel du broker ({size_step}) = 0 "
            f"(taille calculée avant arrondi de pas : {units}) — signal inexécutable, jamais envoyé au broker"
        )
    real_risk = step_rounded_units * stop_distance * pip_value_per_unit
    deviation = abs(real_risk - target_risk_eur) / target_risk_eur if target_risk_eur > 0 else 0.0
    if deviation > max_deviation:
        return False, (
            f"Risque réel estimé après arrondi au pas réel ({size_step}) = {real_risk:.2f}€ "
            f"contre cible {target_risk_eur:.2f}€ — écart {deviation * 100:.1f}% > {max_deviation * 100:.0f}%"
        )
    return True, ""


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

        # Garde-fou de plausibilité de taille (28/08/2026, voir
        # docs/DECISIONS.md, point 3) : `units` ci-dessus est arrondi au
        # pas `min_units` (souvent trop FIN — vérifié en direct contre
        # `minStepDistance` réel sur 4/8 actifs, écart ×10 à ×500). Ne
        # change PAS le sizing réel (invariant #2, pas de fail-safe qui
        # modifierait le risque à la hausse ni de nouvelle valeur
        # imputée) — vérifie seulement, avant tout envoi au broker, que
        # le risque réel une fois arrondi au VRAI pas resterait dans une
        # tolérance de 20% de la cible. Sans effet tant qu'aucun
        # `size_step` n'est renseigné (fail-safe : absence de donnée
        # n'est jamais un motif de rejet).
        plausible, detail = evaluate_sizing_plausibility(
            units, risk_amount_eur, stop_distance, asset_spec.pip_value_per_unit, asset_spec.size_step,
        )
        if not plausible:
            return RiskDecision(approved=False, reason=RiskRejectionReason.POSITION_SIZE_STEP_DEVIATION, detail=detail)

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


def compute_r_multiple(direction: str, entry_price: float, stop_price: float, exit_price: float) -> float:
    """R-multiple d'une sortie unique (§2.1) : distance parcourue
    favorable, exprimée en multiples du risque initial (distance
    entrée-stop). Un stop touché vaut par définition -1R.

    Lève ValueError sur une entrée incohérente (stop_distance <= 0,
    direction inconnue) plutôt que de renvoyer un chiffre trompeur —
    l'appelant (executor.py) est responsable de traiter cette erreur
    comme un échec de clôture à investiguer, jamais comme un R silencieux."""
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        raise ValueError("stop_distance doit être > 0 pour calculer un R-multiple")
    if direction == "long":
        moved = exit_price - entry_price
    elif direction == "short":
        moved = entry_price - exit_price
    else:
        raise ValueError(f"direction inconnue : {direction!r}")
    return moved / stop_distance


def compute_weighted_r_multiple(partial_r_multiples: List[Tuple[float, float]]) -> float:
    """R total sur clôtures partielles (§2.10) : R_total = Σ(fraction ×
    R atteint au palier) — jamais recalculé sur le risque restant après
    une clôture partielle, toujours sur le risque initial (chaque
    R-multiple d'entrée doit déjà avoir été calculé via
    compute_r_multiple avec l'entry_price/stop_price d'origine).

    `partial_r_multiples` est une liste de (fraction, r_multiple) ; les
    fractions doivent sommer à 1.0 (position entièrement close) à
    1e-6 près — sinon ValueError plutôt qu'un total silencieusement
    incomplet ou en double-comptage."""
    total_fraction = sum(fraction for fraction, _ in partial_r_multiples)
    if abs(total_fraction - 1.0) > 1e-6:
        raise ValueError(f"Les fractions doivent sommer à 1.0, obtenu {total_fraction}")
    return sum(fraction * r for fraction, r in partial_r_multiples)
