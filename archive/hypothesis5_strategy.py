"""
ARCHIVÉ le 29/08/2026 — remplacé par src/hypothesis5_strategy_v2.py
(compression -> expansion sur largeur de Bollinger, L5) dans le cadre
de la refonte complète H1-H5 (voir docs/DECISIONS.md, entrée
"Hypothèse #5 — refonte L5" du 29/08/2026, et docs/HYPOTHESES.md,
pré-enregistrement du 29/08/2026). Conservé tel quel pour l'audit —
plus jamais importé par aucun executor. Aucune position H5 v1 n'était
ouverte au moment du remplacement (vérifié), malgré un process
activement déployé sur le VPS depuis le 23/08/2026. Ne pas modifier ce
fichier.

hypothesis5_strategy.py — Hypothèse #5 (régime structurel + momentum
RSI), V3 depuis le 24/08/2026 (voir docs/HYPOTHESES.md/docs/DECISIONS.md
— REMPLACE la version "confluence ICT + RSI" validée le 23/08/2026,
elle-même une redéfinition d'une version encore antérieure jamais
déployée). MODULE CRITIQUE (même exigence de couverture
qu'ict_strategy.py, demande explicite d'Ismaël).

**Révision V3 (24/08/2026)** : la version précédente (régime structurel
+ confluence ICT complète [Fibonacci+FVG] + RSI, trois conditions
réunies) n'a produit AUCUN signal en ~26h de fonctionnement en
production (0 ligne dans `signals`/`trades` pour `source=hypothesis5`) —
motivé par cette absence totale de signal, PAS par un résultat de trade
observé (0 trade, donc aucun ajustement a posteriori sur des données,
cohérent avec invariant #10). La confluence ICT (Fibonacci+FVG) est
retirée : c'était la composante la plus restrictive des trois (H2 seule,
qui la conserve, produit déjà peu de signaux — voir docs/DECISIONS.md du
23/08/2026 sur le régime structurel, un filtre "nettement plus strict
que MA200"). Retirer cette couche évite aussi le doublon avec H2 (qui
reste régime + confluence ICT, inchangée) : H5 devient une hypothèse
distincte (régime + momentum), pas une redite de H2 avec RSI en plus.

**Régime** : structurel (BOS/CHoCH), identique à l'Hypothèse #2 (voir
`ict_strategy.compute_structural_regime`, docs/DECISIONS.md du
23/08/2026) — jamais MA200, jamais recalculé différemment ici.

**Déclencheur (V3)** : régime structurel confirmé (BOS/CHoCH) ET momentum
RSI(14) franchissant le seuil 50 dans le sens de la structure, AU MÊME
MOMENT (entre l'avant-dernière et la dernière bougie fournies — condition
de franchissement strict, INCHANGÉE depuis la version précédente, voir
`_rsi_just_crossed_threshold`). `ict_strategy.compute_structural_entry`
(régime + jambe d'impulsion, SANS confluence Fibonacci/FVG — extraite le
24/08/2026 de `evaluate_entry` pour cet usage, voir docs/DECISIONS.md)
fournit entrée/stop ; seul le filtre RSI est ajouté par ce module.

**Précision terminologique** : la demande de cette révision (24/08/2026)
mentionnait une "fenêtre de 3 bougies déjà en place" pour le
franchissement RSI — vérifié en relisant le code, ce n'est pas exact :
`_rsi_just_crossed_threshold` compare seulement les DEUX dernières
bougies (franchissement strict entre avant-dernière et dernière), inchangé
depuis la V2, jamais une fenêtre de 3. Conservé tel quel ici (aucune
fenêtre plus large n'était réellement en place à élargir) — voir
docs/DECISIONS.md pour la note complète ; si une fenêtre de tolérance
plus large est réellement voulue, ce serait un nouveau paramètre à
justifier séparément (invariant #10), pas fait ici.

**Limite assumée, documentée explicitement (pas une simplification
cachée)** : combiner régime structurel et momentum RSI dans une même
condition d'entrée empêche d'attribuer un résultat à l'un ou l'autre
facteur séparément. Accepté sciemment (voir docs/HYPOTHESES.md) : H5
teste la COMBINAISON comme hypothèse à part entière.

**Sortie** : mécanisme §2.10 déjà construit pour Station X — TP1 50% à
1R, TP2 30% à 2R, TP3 20% sous trailing 2×ATR(14) plancher breakeven,
stop déplacé au breakeven dès TP1 touché (resserrement seulement,
invariant #5). Dispatché automatiquement par `executor._evaluate_
position_management` dès que `signals.tp1`/`tp2` sont renseignés —
INCHANGÉ par la révision V3 (seul le déclencheur change, pas la sortie).
R est TOUJOURS calculé sur le risque initial DE CE TRADE H5
(`|entry_price - stop_price|` du signal structurel reçu), jamais
recalculé après une clôture partielle (§2.1), jamais lu ailleurs.

**Indépendance totale vis-à-vis de Station X** (vérifiée explicitement,
voir docs/DECISIONS.md) : ce module ne lit AUCUNE valeur (TP, R, niveau,
prix) depuis la table `signals` de Station X ni depuis aucun trade
Station X — il n'ouvre même aucune connexion base de données. Toutes les
valeurs proviennent exclusivement des bougies H5 elles-mêmes (`candles`,
récupérées en direct via `market_data.get_candles` sur le COMPTE et
l'ACTIF de H5) et de l'entrée/stop que `ict_strategy.compute_structural_
entry` calcule à partir de CES MÊMES bougies. Le mécanisme de sortie
§2.10 réutilisé (fractions 50/30/20, formule de trailing 2×ATR) est une
pure mécanique de calcul, appliquée aux entrée/stop/ATR propres à CE
trade H5 — jamais à une valeur lue sur un trade ou un signal Station X.

Aucun LLM (invariant #1) : traduction numérique déterministe, fail-safe
(invariant #7 — toute erreur interne devient "pas de signal", même
patron qu'ict_strategy.evaluate_entry/compute_structural_entry).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.ict_strategy import compute_structural_entry as _compute_structural_entry
from src.market_data import Candle

# Paramètres propres à cette hypothèse (§2.11, cap 2-3, exactement 3/3 —
# voir docs/HYPOTHESES.md pour le budget détaillé) :
# - distance de TP1/TP2 en multiples de R (R = |entrée - stop initial|),
#   valeurs rondes choisies a priori, jamais ajustées aux données ;
# - config RSI (période + seuil), regroupés en UN seul paramètre —
#   même convention que la "config des bandes" de l'Hypothèse #4
#   (période + multiplicateur comptés ensemble, jamais séparément).
# Le régime structurel (BOS/CHoCH, K=2 pour les swings de la jambe) est
# hérité de H2, jamais "dépensé" une seconde fois — même précédent que H3
# réutilisant MA200/Donchian(20) de H1 (voir docs/HYPOTHESES.md,
# correction du modèle de budget du 21/08/2026). Depuis la V3
# (24/08/2026), H2 est la SEULE hypothèse à ajouter la confluence
# Fibonacci/FVG par-dessus ce régime — H5 ne la réutilise plus.
TP1_R_MULTIPLE = 1.0
TP2_R_MULTIPLE = 2.0
RSI_PERIOD = 14
RSI_THRESHOLD = 50.0


@dataclass(frozen=True)
class Hypothesis5Signal:
    asset: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float
    confidence: float = 1.0  # déterministe par construction — voir docs/HYPOTHESES.md


def compute_rsi(candles: List[Candle], period: int = RSI_PERIOD) -> Optional[float]:
    """RSI de Wilder (lissage exponentiel 1/period sur les gains/pertes
    moyens de clôture à clôture — définition standard, même méthode que
    `market_data.compute_atr`, pas une formule inventée pour ce projet).
    Retourne None si moins de `period + 1` bougies (pas assez d'historique
    pour un premier calcul), même contrat que `compute_atr`."""
    if len(candles) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(candles)):
        delta = candles[i].close - candles[i - 1].close
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _rsi_just_crossed_threshold(candles: List[Candle], direction: str) -> bool:
    """Vrai si le RSI(14) vient de franchir le seuil 50 DANS le sens de
    `direction`, entre l'avant-dernière et la dernière bougie fournies —
    "au même moment" que la confluence ICT évaluée sur ces mêmes bougies
    (§ docstring du module). Un RSI déjà au-dessus/en dessous de 50
    depuis plusieurs bougies (pas un franchissement récent) ne compte
    PAS — condition volontairement stricte, cohérente avec "franchissant
    le seuil" (pas "étant du bon côté du seuil")."""
    rsi_now = compute_rsi(candles, RSI_PERIOD)
    rsi_prev = compute_rsi(candles[:-1], RSI_PERIOD)
    if rsi_now is None or rsi_prev is None:
        return False
    if direction == "long":
        return rsi_prev < RSI_THRESHOLD <= rsi_now
    if direction == "short":
        return rsi_prev > RSI_THRESHOLD >= rsi_now
    raise ValueError(f"direction inconnue : {direction!r}")


def _compute_tp_levels(direction: str, entry_price: float, stop_price: float) -> Tuple[float, float]:
    r = abs(entry_price - stop_price)
    if direction == "long":
        return entry_price + TP1_R_MULTIPLE * r, entry_price + TP2_R_MULTIPLE * r
    if direction == "short":
        return entry_price - TP1_R_MULTIPLE * r, entry_price - TP2_R_MULTIPLE * r
    raise ValueError(f"direction inconnue : {direction!r}")


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[Hypothesis5Signal]:
    """Point d'entrée unique de l'Hypothèse #5 (V3, 24/08/2026) : délègue
    le régime structurel et la jambe d'impulsion à `ict_strategy.
    compute_structural_entry` (régime+jambe SANS confluence Fibonacci/FVG
    — voir docstring du module), exige EN PLUS un franchissement du seuil
    50 du RSI(14) dans le même sens sur la même bougie, puis calcule
    TP1(1R)/TP2(2R) sur le risque initial du signal structurel obtenu.

    Ne lève jamais d'exception : toute erreur interne devient "pas de
    signal" (fail-safe, invariant #7)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[Hypothesis5Signal]:
    structural_signal = _compute_structural_entry(asset, candles)
    if structural_signal is None:
        return None
    if not _rsi_just_crossed_threshold(candles, structural_signal.direction):
        return None
    tp1, tp2 = _compute_tp_levels(
        structural_signal.direction, structural_signal.entry_price, structural_signal.stop_price,
    )
    return Hypothesis5Signal(
        asset=asset,
        direction=structural_signal.direction,
        entry_price=structural_signal.entry_price,
        stop_price=structural_signal.stop_price,
        tp1=tp1,
        tp2=tp2,
        confidence=structural_signal.confidence,
    )
