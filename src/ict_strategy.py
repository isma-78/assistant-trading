"""
ict_strategy.py — Hypothèse #2 (ICT / Smart Money Concepts), Option B,
validée par Ismaël le 21/08/2026 (docs/HYPOTHESES.md). MODULE CRITIQUE
(même exigence de couverture que trend_strategy.py, demande explicite
d'Ismaël).

**Bascule du régime, 23/08/2026 (voir docs/DECISIONS.md)** : le régime
de fond n'utilise plus `trend_strategy.compute_regime` (MA200) — il
utilise désormais `classify_structure_break` (BOS/CHoCH), déjà codée et
testée depuis la première version de ce module mais jamais branchée
comme condition de décision. Motivé par la cohérence théorique (une
vraie lecture de structure de marché ICT, pas un filtre de tendance
générique emprunté à une autre hypothèse) — décidé AVANT tout résultat
réel, aucun trade H2 n'a été regardé pour prendre cette décision (voir
`compute_structural_regime` ci-dessous pour la traduction mécanique
exacte et la justification de sa conception). Le trailing Donchian(20)
de sortie, lui, reste inchangé (réutilisé de trend_strategy.py, comme
avant).

Ajoute une couche de confluence ICT pour le déclencheur (INCHANGÉE par
la bascule ci-dessus) : swings fractals (Bill Williams, K=2) comme
ancrage des retracements de Fibonacci (61,8 %-78,6 %, valeurs prescrites
littéralement par le CDC §3.3), confirmés par un FVG (Fair Value Gap)
chevauchant la zone. Voir docs/HYPOTHESES.md (entrées du 20 et
21/08/2026) pour la justification théorique complète, les angles morts
assumés, et le budget de variables (K=2 est le seul paramètre propre à
cette hypothèse, 1/3 de son budget §2.11 — voir la correction du modèle
de budget du 21/08/2026).

**Traduction mécanique Option B — écrite ici, PAS entièrement détaillée
dans la proposition d'origine du 20/08/2026** (qui ne détaillait la
règle d'entrée complète, point 4, que pour l'Option A). Trois choix de
conception concrets, faits en écrivant ce module, à vérifier par Ismaël
avant observation de résultats (aucune donnée regardée avant ces choix) :

1. **Sélection de la jambe d'impulsion** : parmi les swings CONFIRMÉS de
   la fenêtre récente, on prend le dernier swing bas confirmé puis le
   premier swing haut confirmé plus récent que lui (régime haussier —
   symétrique en régime baissier). S'il n'existe aucune paire valide, pas
   de signal. Choix motivé par la simplicité et la cohérence avec le
   biais de régime déjà établi, pas dérivé d'une règle ICT canonique
   (aucune n'existe pour ce choix précis, voir docs/HYPOTHESES.md).
2. **Fenêtre de recherche** : swings et FVG cherchés sur les mêmes
   20 dernières bougies que le canal de Donchian de l'Hypothèse #1
   (`DONCHIAN_PERIOD`, réutilisé, PAS un nouveau paramètre — même
   raisonnement que le point 1 de la proposition d'origine pour les
   FVG). Une marge de `2×FRACTAL_K` bougies supplémentaires est ajoutée
   en amont pour permettre la confirmation des swings en bord de fenêtre.
3. **BOS/CHoCH (point 3 de la proposition) implémenté mais PAS câblé
   comme condition d'entrée** dans cette première version — la
   proposition validée ne spécifiait la règle d'entrée complète (point 4)
   que pour l'Option A, qui ne s'appuyait sur aucune cassure de
   structure. `classify_structure_break` existe, testée à 100%, mais
   n'est appelée par aucune fonction de décision ici — l'ajouter comme
   condition supplémentaire serait une complexité non validée par
   Ismaël, pas une simple traduction de ce qui a été approuvé.

Stop initial : le swing OPPOSÉ à la direction (swing bas pour un long,
swing haut pour un short) — traduction directe de "stop = borne opposée
du canal" (Option A) une fois le canal remplacé par la jambe de swings.

Sortie : trailing sur `trend_strategy.compute_trailing_stop_channel`,
réutilisé tel quel (décision explicite et inconditionnelle de la
proposition d'origine, valable pour les deux options) — câblé
automatiquement par `executor._evaluate_position_management` via
`state.tp1 is None` et `executor._TREND_CANDLE_RESOLUTION[HYPOTHESIS2_SOURCE]`
("HOUR"), aucune fonction supplémentaire nécessaire ici.

Aucun LLM (invariant #1) : comparaisons numériques déterministes,
fail-safe (invariant #7 — toute erreur interne devient "pas de signal").
"""

from typing import List, Optional, Tuple

from src.market_data import Candle
from src.trend_strategy import DONCHIAN_PERIOD, TrendSignal

# Seul paramètre propre à cette hypothèse (§2.11, cap 2-3 — voir
# docs/HYPOTHESES.md, correction du modèle de budget du 21/08/2026).
# K=2 : "fractale à 5 bougies", convention classique (Bill Williams,
# Trading Chaos, 1995).
FRACTAL_K = 2

# Fenêtre de recherche des swings/FVG — réutilise DONCHIAN_PERIOD (20),
# PAS un nouveau paramètre (voir docstring du module, point 2).
RECENT_WINDOW = DONCHIAN_PERIOD

# Ratios de Fibonacci prescrits littéralement par le CDC §3.3 (50%,
# 61,8%, 78,6%) — mêmes constantes que l'Option A de la proposition
# d'origine, jamais un choix a priori de ma part.
_FIB_RATIO_LOW = 0.618
_FIB_RATIO_HIGH = 0.786


def find_confirmed_swings(candles: List[Candle], k: int = FRACTAL_K) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Swings fractals confirmés (Bill Williams) : un plus haut à l'index
    `i` est confirmé si `high(i)` dépasse strictement les `k` plus hauts
    de chaque côté ; symétrique pour un plus bas. Retourne
    (swing_highs, swing_lows), chacun une liste de (index, prix) dans
    l'ordre chronologique. Les `k` premières et `k` dernières bougies ne
    peuvent jamais produire de swing confirmé (pas assez de contexte des
    deux côtés) — comportement voulu, pas une limite à contourner."""
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    n = len(candles)
    for i in range(k, n - k):
        neighborhood = list(range(i - k, i + k + 1))
        neighborhood.remove(i)
        if candles[i].high > max(candles[j].high for j in neighborhood):
            highs.append((i, candles[i].high))
        if candles[i].low < min(candles[j].low for j in neighborhood):
            lows.append((i, candles[i].low))
    return highs, lows


def find_fvgs(candles: List[Candle]) -> List[Tuple[str, float, float]]:
    """FVG (point 1 de la proposition, docs/HYPOTHESES.md) : sur 3
    bougies consécutives C1,C2,C3, un FVG haussier existe si
    `low(C3) > high(C1)` (zone [high(C1), low(C3)]), un FVG baissier si
    `high(C3) < low(C1)` (zone [high(C3), low(C1)]). Aucun seuil de
    taille minimale (décision assumée de la proposition d'origine).
    Retourne une liste de (direction, bas_zone, haut_zone)."""
    fvgs: List[Tuple[str, float, float]] = []
    for i in range(2, len(candles)):
        c1, c3 = candles[i - 2], candles[i]
        if c3.low > c1.high:
            fvgs.append(("long", c1.high, c3.low))
        elif c3.high < c1.low:
            fvgs.append(("short", c3.high, c1.low))
    return fvgs


def compute_fibonacci_zone(direction: str, swing_low: float, swing_high: float) -> Tuple[float, float]:
    """Zone de retracement 61,8%-78,6% (CDC §3.3) entre `swing_low` et
    `swing_high` (swing_high > swing_low toujours garanti par l'appelant).
    Retourne (bas_zone, haut_zone), toujours bas_zone <= haut_zone."""
    if swing_high <= swing_low:
        raise ValueError("swing_high doit être strictement > swing_low")
    span = swing_high - swing_low
    if direction == "long":
        return swing_high - _FIB_RATIO_HIGH * span, swing_high - _FIB_RATIO_LOW * span
    if direction == "short":
        return swing_low + _FIB_RATIO_LOW * span, swing_low + _FIB_RATIO_HIGH * span
    raise ValueError(f"direction inconnue : {direction!r}")


def classify_structure_break(
    current_close: float, swing_highs: List[Tuple[int, float]], swing_lows: List[Tuple[int, float]], bias: str,
) -> Optional[str]:
    """Point 3 de la proposition d'origine : BOS (poursuite) si la
    clôture dépasse le dernier swing dans le sens du biais établi, CHoCH
    (retournement) si elle dépasse le dernier swing dans le sens opposé.
    None si ni l'un ni l'autre. **Non appelée par evaluate_entry dans
    cette première version** (voir docstring du module, point 3) — capacité
    disponible et testée, pas encore une condition de décision."""
    if bias == "long":
        if swing_highs and current_close > swing_highs[-1][1]:
            return "BOS"
        if swing_lows and current_close < swing_lows[-1][1]:
            return "CHoCH"
    elif bias == "short":
        if swing_lows and current_close < swing_lows[-1][1]:
            return "BOS"
        if swing_highs and current_close > swing_highs[-1][1]:
            return "CHoCH"
    else:
        raise ValueError(f"biais inconnu : {bias!r}")
    return None


def compute_structural_regime(
    swing_highs: List[Tuple[int, float]], swing_lows: List[Tuple[int, float]], current_close: float,
) -> Optional[str]:
    """Régime de fond de l'Hypothèse #2 (remplace `trend_strategy.
    compute_regime`/MA200, bascule du 23/08/2026 — voir docs/DECISIONS.md
    et la docstring du module). Réutilise `classify_structure_break`
    EXACTEMENT telle que codée, sans nouvelle logique de cassure.

    Stateless (même contrat que `compute_regime` qu'elle remplace :
    aucun biais antérieur mémorisé d'un appel à l'autre, aucun état
    conservé entre deux évaluations). Un biais CANDIDAT est essayé dans
    chaque sens ; celui qui produit un BOS (poursuite confirmée dans CE
    sens — la clôture courante dépasse le dernier swing dans le sens
    testé) est retenu comme régime structurel courant :
    - `classify_structure_break(close, ..., "long") == "BOS"` signifie
      `close > swing_highs[-1]` : la clôture dépasse le dernier plus haut
      confirmé -> régime haussier confirmé.
    - `classify_structure_break(close, ..., "short") == "BOS"` signifie
      `close < swing_lows[-1]` : la clôture dépasse le dernier plus bas
      confirmé -> régime baissier confirmé.
    Ces deux tests sont mutuellement exclusifs (la clôture ne peut pas
    être à la fois au-dessus du dernier plus haut ET en dessous du
    dernier plus bas) : aucune ambiguïté possible, jamais besoin
    d'examiner les branches CHoCH (un CHoCH sous un biais candidat
    correspond toujours au BOS symétrique sous le biais opposé — même
    cassure, juste un label différent selon le biais testé).

    None si aucun swing confirmé des deux côtés, ou si la clôture reste
    à l'intérieur de la fourchette du dernier plus haut/plus bas (ni
    cassure haussière ni baissière — structure "en range", pas de
    régime tranché).

    Conséquence assumée, documentée dans docs/DECISIONS.md : ce test
    porte sur les DERNIERS swings confirmés de la fenêtre, pas
    nécessairement ceux de la jambe utilisée ensuite pour la zone de
    Fibonacci (`_find_latest_valid_leg` peut sélectionner une jambe plus
    ancienne) — une clôture strictement À L'INTÉRIEUR de la zone de
    retracement d'UNE jambe ne peut, par construction, jamais constituer
    un BOS/CHoCH de CETTE MÊME jambe (la zone 61,8-78,6% est toujours
    strictement comprise entre son propre swing bas et son swing haut).
    Un signal H2 valide exige donc une cassure structurelle distincte,
    plus récente que la jambe d'entrée elle-même — un filtre nettement
    plus strict que MA200 (qui ne dépendait d'aucune action récente du
    prix), qui réduit mécaniquement la fréquence des signaux. Accepté
    comme le prix normal d'une lecture de structure plus fidèle à la
    théorie ICT, pas corrigé ici."""
    if not swing_highs or not swing_lows:
        return None
    if classify_structure_break(current_close, swing_highs, swing_lows, "long") == "BOS":
        return "long"
    if classify_structure_break(current_close, swing_highs, swing_lows, "short") == "BOS":
        return "short"
    return None


def _find_latest_valid_leg(
    swing_highs: List[Tuple[int, float]], swing_lows: List[Tuple[int, float]], direction: str,
) -> Optional[Tuple[float, float]]:
    """Jambe d'impulsion la plus récente dans le sens de `direction`
    (voir docstring du module, point 1). Retourne (swing_low, swing_high)
    ou None si aucune paire valide."""
    if not swing_highs or not swing_lows:
        return None
    if direction == "long":
        low_idx, low_price = swing_lows[-1]
        candidates = [(i, p) for i, p in swing_highs if i > low_idx and p > low_price]
        if not candidates:
            return None
        _, high_price = candidates[0]
        return low_price, high_price
    if direction == "short":
        high_idx, high_price = swing_highs[-1]
        candidates = [(i, p) for i, p in swing_lows if i > high_idx and p < high_price]
        if not candidates:
            return None
        _, low_price = candidates[0]
        return low_price, high_price
    raise ValueError(f"direction inconnue : {direction!r}")


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    """Point d'entrée unique de l'Hypothèse #2 : régime structurel BOS/
    CHoCH (`compute_structural_regime`, bascule du 23/08/2026 — voir
    docstring du module), jambe de swings fractals, zone de confluence
    Fibonacci, FVG chevauchant la zone dans le sens du régime.

    Ne lève jamais d'exception : toute erreur interne devient "pas de
    signal" (fail-safe, invariant #7 — même patron que trend_strategy)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    # Garde de longueur minimale : uniquement ce dont find_confirmed_swings
    # a besoin (RECENT_WINDOW + marge de confirmation des swings en bord de
    # fenêtre) — plus de dépendance à MA_PERIOD (200) depuis la bascule du
    # 23/08/2026, le régime ne s'appuie plus sur une moyenne mobile longue.
    window_size = RECENT_WINDOW + 2 * FRACTAL_K + 1
    if len(candles) < window_size:
        return None
    recent = candles[-window_size:]

    swing_highs, swing_lows = find_confirmed_swings(recent, FRACTAL_K)
    current_close = candles[-1].close
    regime = compute_structural_regime(swing_highs, swing_lows, current_close)
    if regime is None:
        return None

    leg = _find_latest_valid_leg(swing_highs, swing_lows, regime)
    if leg is None:
        return None
    swing_low, swing_high = leg

    zone_low, zone_high = compute_fibonacci_zone(regime, swing_low, swing_high)
    if not (zone_low <= current_close <= zone_high):
        return None

    fvgs = find_fvgs(recent)
    overlap = any(
        d == regime and not (high < zone_low or low > zone_high)
        for d, low, high in fvgs
    )
    if not overlap:
        return None

    stop_price = swing_low if regime == "long" else swing_high
    return TrendSignal(asset=asset, direction=regime, entry_price=current_close, stop_price=stop_price)
