"""
mean_reversion_strategy.py — Stratégie technique complémentaire. MODULE
CRITIQUE (§2.11, §4.4 du CDC : même exigence de couverture que
trend_strategy.py/ict_strategy.py — demande explicite d'Ismaël pour ce
palier).

Implémente l'Hypothèse #4 de docs/HYPOTHESES.md, VALIDÉE par Ismaël le
21/08/2026 (autorisée en démo, aucun capital réel — voir docs/DECISIONS.md
pour la décision sur le plafond §3.9 : toute future évaluation/validation
d'une hypothèse appliquera désormais la correction pour comparaisons
multiples calibrée sur 4 hypothèses, pas 3) : filtre de régime MA(200)
horaire (réutilisé tel quel depuis trend_strategy.py, jamais recalculé
différemment) + déclencheur de retour à la moyenne sur Bandes de
Bollinger(20, 2σ) horaires. On ne fade jamais contre le régime de fond,
seulement les extensions à court terme à l'intérieur du régime autorisé.

Deux choix de calcul non spécifiés par la proposition initiale,
tranchés par Ismaël le 21/08/2026 (voir docs/HYPOTHESES.md/DECISIONS.md) :
- "Largeur de bande" pour le stop = DEMI-écart (médiane -> bande = 2σ),
  pas l'écart complet (4σ) retenu par erreur dans la première version.
  Voir STOP_WIDTH_MULTIPLIER — stop et cible désormais symétriques
  (R:R ≈ 1:1), au lieu du 2:1 défavorable de la version initiale.
- Écart-type de POPULATION (division par BOLLINGER_PERIOD), pas
  d'échantillon — convention Bollinger standard, confirmé sans changement.

Contrairement à trend_strategy.py (Hypothèse #1) : sortie par
take-profit fixe (bande médiane figée à l'entrée), stop fixe, AUCUN
trailing (conforme invariant #5 : un stop qui ne bouge jamais ne peut
jamais être élargi). MeanReversionSignal porte donc un champ
take_profit que TrendSignal n'a pas — la gestion de position
correspondante (TP unique, fermeture à 100%, pas de trailing) ne
s'intègre pas dans l'un des deux patrons existants de
executor._evaluate_position_management ; voir docs/HYPOTHESES.md pour
le détail de cet écart, non implémenté ici (module de signal/sizing
seul, pas de câblage d'exécution).

Aucun LLM (invariant #1) : comparaisons numériques déterministes,
fail-safe (invariant #7 — toute erreur interne devient "pas de signal",
jamais un signal potentiellement invalide).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.market_data import Candle
from src.trend_strategy import MA_PERIOD, TrendSignal, compute_regime, compute_tp_levels

BOLLINGER_PERIOD = 20      # Paramètre #1 (avec le multiplicateur d'écart-type) — voir docs/HYPOTHESES.md
BOLLINGER_STD_MULTIPLIER = 2.0  # Paramètre #1 (couplé à BOLLINGER_PERIOD, jamais ajusté séparément)

# Paramètre #2 — décision d'Ismaël (21/08/2026, voir docs/HYPOTHESES.md et
# docs/DECISIONS.md) : appliqué à la DEMI-largeur de bande (médiane -> bande,
# soit 1 écart-type × BOLLINGER_STD_MULTIPLIER = 2σ), PAS la largeur complète
# (haute − basse = 4σ) retenue par erreur dans la proposition initiale. Avec
# TP à la bande médiane (2σ de la clôture d'entrée) et ce choix, stop et
# cible sont symétriques : R:R ≈ 1:1, au lieu du 2:1 défavorable de la
# première version (stop deux fois plus large que la cible).
STOP_WIDTH_MULTIPLIER = 1.0

__all__ = [
    "MA_PERIOD",
    "BOLLINGER_PERIOD",
    "BOLLINGER_STD_MULTIPLIER",
    "STOP_WIDTH_MULTIPLIER",
    "MeanReversionSignal",
    "compute_bollinger_bands",
    "evaluate_entry",
]


@dataclass(frozen=True)
class MeanReversionSignal:
    asset: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_price: float
    take_profit: float
    confidence: float = 1.0  # déterministe par construction — voir docs/HYPOTHESES.md


def compute_bollinger_bands(
    candles: List[Candle], period: int = BOLLINGER_PERIOD, std_multiplier: float = BOLLINGER_STD_MULTIPLIER,
) -> Optional[Tuple[float, float, float]]:
    """Bandes de Bollinger sur `period` bougies, INCLUANT la bougie
    courante (candles[-1]) — même convention que trend_strategy.
    compute_regime (la MA de régime inclut aussi la bougie courante),
    volontairement distincte de compute_donchian_channel qui exclut la
    bougie courante (le canal de rupture doit précéder le prix testé ;
    ici la bande elle-même est le repère testé, elle doit donc inclure
    le point testé, comme toute implémentation standard des Bandes de
    Bollinger).

    Retourne (bande_haute, bande_médiane, bande_basse), écart-type de
    POPULATION (division par `period`, pas `period - 1` — voir
    docs/HYPOTHESES.md). None si pas assez d'historique."""
    if len(candles) < period:
        return None
    window = [c.close for c in candles[-period:]]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    std = variance ** 0.5
    upper = middle + std_multiplier * std
    lower = middle - std_multiplier * std
    return upper, middle, lower


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[MeanReversionSignal]:
    """Point d'entrée unique (§2.11) : régime MA(200) PUIS toucher de la
    bande de Bollinger opposée au régime (retour à la moyenne, jamais
    contre le régime). TP = bande médiane au moment de l'entrée, figée.
    Stop = entrée ∓ STOP_WIDTH_MULTIPLIER × DEMI-largeur de la bande
    (médiane -> bande, décision d'Ismaël du 21/08/2026 — voir
    docs/HYPOTHESES.md) : stop et cible symétriques par construction
    (R:R ≈ 1:1 avec STOP_WIDTH_MULTIPLIER=1.0).

    Ne lève jamais d'exception : toute erreur interne devient "pas de
    signal" plutôt qu'un signal potentiellement invalide (fail-safe,
    invariant #7 — même patron que trend_strategy/ict_strategy)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[MeanReversionSignal]:
    regime = compute_regime(candles)
    if regime is None:
        return None

    bands = compute_bollinger_bands(candles)
    if bands is None:
        return None
    upper, middle, lower = bands
    half_width = (upper - lower) / 2  # médiane -> bande = 1 écart-type × BOLLINGER_STD_MULTIPLIER
    if half_width <= 0:
        return None

    current_close = candles[-1].close

    if regime == "long" and current_close <= lower:
        stop_price = current_close - STOP_WIDTH_MULTIPLIER * half_width
        return MeanReversionSignal(
            asset=asset, direction="long", entry_price=current_close,
            stop_price=stop_price, take_profit=middle,
        )

    if regime == "short" and current_close >= upper:
        stop_price = current_close + STOP_WIDTH_MULTIPLIER * half_width
        return MeanReversionSignal(
            asset=asset, direction="short", entry_price=current_close,
            stop_price=stop_price, take_profit=middle,
        )

    return None


# ---------------------------------------------------------------------------
# Candidat "breakout de volatilité (squeeze Bollinger)" — nouvelle
# hypothèse pour la place de H4, pré-enregistrée dans docs/HYPOTHESES.md
# (25/08/2026, "trois chantiers") AVANT tout calcul. Logique DISTINCTE de
# `evaluate_entry` ci-dessus (retour à la moyenne) — pas une variante,
# une famille de déclencheur différente (cassure après compression de
# volatilité, pas retour à la moyenne). Réutilise `compute_bollinger_
# bands` À L'IDENTIQUE (même fonction, mêmes constantes) pour la config
# Bollinger — non recomptée dans le budget de cette hypothèse (même
# précédent que H3 réutilisant MA200/Donchian de H1). Budget propre à ce
# candidat : 4 variables (percentile + fenêtre de compression, TP1, TP2 —
# voir docs/HYPOTHESES.md pour le décompte honnête, la demande en citait
# 2, TP1/TP2 comptent séparément par la convention déjà établie du
# projet). PAS de filtre MA200 (décision explicite d'Ismaël) : aucune
# confirmation de régime croisée n'a de sens ici, jamais câblée avec
# require_regime_confirmation=True pour ce candidat.
# ---------------------------------------------------------------------------

SQUEEZE_LOOKBACK_PERIODS = 100  # fenêtre glissante de largeurs de bande sur laquelle le percentile est calculé
SQUEEZE_PERCENTILE = 20         # compression = largeur au 20e percentile le plus bas de cette fenêtre
SQUEEZE_TP1_R_MULTIPLE = 1.0    # même convention a priori que H2/H3/H5 (mécanisme §2.10), choisie indépendamment
SQUEEZE_TP2_R_MULTIPLE = 2.0

__all__.extend([
    "SQUEEZE_LOOKBACK_PERIODS",
    "SQUEEZE_PERCENTILE",
    "SQUEEZE_TP1_R_MULTIPLE",
    "SQUEEZE_TP2_R_MULTIPLE",
    "evaluate_entry_squeeze_breakout",
])


def _rolling_bollinger_bandwidths(
    candles: List[Candle], period: int = BOLLINGER_PERIOD, std_multiplier: float = BOLLINGER_STD_MULTIPLIER,
) -> List[float]:
    """Largeur de bande (haute − basse) de Bollinger(period, std_multiplier)
    calculée à CHAQUE point possible de `candles` (chronologique, un
    point par bougie à partir de l'index period-1), calcul incrémental
    (somme/somme des carrés glissantes) — même formule que
    `compute_bollinger_bands` (écart-type de POPULATION), jamais une
    redéfinition indépendante. O(len(candles)), pas O(len(candles) ×
    period) — nécessaire ici car appelée à chaque bougie du backtest."""
    if len(candles) < period:
        return []
    closes = [c.close for c in candles]
    widths: List[float] = []
    window_sum = sum(closes[:period])
    window_sq_sum = sum(c * c for c in closes[:period])
    for end in range(period, len(closes) + 1):
        mean = window_sum / period
        variance = max(window_sq_sum / period - mean * mean, 0.0)  # garde contre un résidu négatif de précision flottante
        std = variance ** 0.5
        widths.append(2 * std_multiplier * std)
        if end < len(closes):
            outgoing, incoming = closes[end - period], closes[end]
            window_sum += incoming - outgoing
            window_sq_sum += incoming * incoming - outgoing * outgoing
    return widths


def _percentile(values: List[float], pct: float) -> float:
    """Percentile par interpolation linéaire (méthode "linear", même
    convention que numpy.percentile par défaut) — déterministe, aucune
    dépendance statistique ajoutée pour ce besoin ponctuel."""
    ordered = sorted(values)
    index = (pct / 100.0) * (len(ordered) - 1)
    lower_idx = int(index)
    upper_idx = min(lower_idx + 1, len(ordered) - 1)
    frac = index - lower_idx
    return ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx])


def evaluate_entry_squeeze_breakout(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    """Point d'entrée du candidat "breakout de volatilité" (voir
    docs/HYPOTHESES.md, 25/08/2026) : compression de bande Bollinger
    (largeur au 20e percentile le plus bas des 100 dernières périodes) À
    LA BOUGIE PRÉCÉDENTE, PUIS clôture au-delà de la bande à la bougie
    courante — direction = sens de la cassure. Stop = bande médiane
    (SMA) au moment de la cassure (invalidation naturelle, aucune
    nouvelle constante). TP1(1R)/TP2(2R) + trailing du reliquat, mêmes
    mécanismes §2.10 que H2/H3/H5.

    Ne lève jamais d'exception : toute erreur interne devient "pas de
    signal" (fail-safe, invariant #7 — même patron que evaluate_entry
    ci-dessus)."""
    try:
        return _evaluate_entry_squeeze_breakout(asset, candles)
    except Exception:
        return None


def _evaluate_entry_squeeze_breakout(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    min_needed = BOLLINGER_PERIOD + SQUEEZE_LOOKBACK_PERIODS + 1
    if len(candles) < min_needed:
        return None

    # Série de largeurs de bande jusqu'à la bougie PRÉCÉDENTE incluse
    # (candles[:-1]) — la compression s'évalue AVANT la bougie de cassure,
    # jamais en incluant celle-ci (sinon la cassure elle-même, qui élargit
    # mécaniquement la bande, fausserait le percentile).
    widths = _rolling_bollinger_bandwidths(candles[:-1])
    if len(widths) < SQUEEZE_LOOKBACK_PERIODS:
        return None
    recent_widths = widths[-SQUEEZE_LOOKBACK_PERIODS:]
    threshold = _percentile(recent_widths, SQUEEZE_PERCENTILE)
    width_prev = widths[-1]
    if width_prev > threshold:
        return None  # pas de compression active à la bougie précédente

    bands = compute_bollinger_bands(candles)
    if bands is None:
        return None
    upper, middle, lower = bands
    current_close = candles[-1].close

    if current_close > upper:
        direction = "long"
    elif current_close < lower:
        direction = "short"
    else:
        return None

    stop_price = middle
    if stop_price == current_close:
        return None  # risque initial nul, garde fail-safe (même patron que half_width<=0 ci-dessus)

    tp1, tp2 = compute_tp_levels(direction, current_close, stop_price, SQUEEZE_TP1_R_MULTIPLE, SQUEEZE_TP2_R_MULTIPLE)
    return TrendSignal(
        asset=asset, direction=direction, entry_price=current_close,
        stop_price=stop_price, tp1=tp1, tp2=tp2,
    )
