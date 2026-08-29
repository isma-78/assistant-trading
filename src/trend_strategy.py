"""
`evaluate_entry`/`compute_regime` DÉPRÉCIÉES pour le live depuis le
29/08/2026 — remplacées par `hypothesis1_strategy_v2.evaluate_entry`
(régime ADX, L1, voir docs/HYPOTHESES.md/docs/DECISIONS.md). Ce fichier
N'EST PAS archivé : `TrendSignal`, `compute_tp_levels`,
`compute_donchian_channel`, `compute_trailing_stop_channel` restent des
utilitaires partagés activement par TOUTES les hypothèses (H1-H5).
Seules `evaluate_entry`/`compute_regime` (le déclencheur MA200+Donchian
d'origine) ne sont plus importées par aucun executor.

trend_strategy.py — Stratégie technique complémentaire. MODULE CRITIQUE
(§2.11, §4.4 du CDC : module `trend_strategy`, 0% LLM — même exigence de
couverture que risk_engine.py, demande explicite d'Ismaël pour ce
palier).

Implémente l'Hypothèse #1 de docs/HYPOTHESES.md (20/08/2026, validée) :
filtre de régime MA(200) + déclencheur de rupture de canal de
Donchian(20). Source indépendante du canal Station X, pour accumuler des
données de trading démo sur les actifs de la liste blanche peu ou pas
couverts par le canal (§2.11 : "Métriques calculées séparément par
source").

MA_PERIOD et DONCHIAN_PERIOD sont FIGÉS ici, choisis a priori et
documentés dans docs/HYPOTHESES.md — ne jamais les modifier sans une
nouvelle entrée datée dans ce fichier et un redéploiement (jamais un
ajustement automatique ni en direct, invariant #10).

Aucun LLM (invariant #1) : deux comparaisons numériques déterministes,
fail-safe (invariant #7 — toute erreur interne devient "pas de signal",
jamais un signal potentiellement invalide).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.market_data import Candle

MA_PERIOD = 200       # §2.11 du CDC : valeur prescrite littéralement, pas un paramètre ajustable
DONCHIAN_PERIOD = 20  # Seul paramètre réglable de l'Hypothèse #1 (docs/HYPOTHESES.md) — sert aussi au stop


@dataclass(frozen=True)
class TrendSignal:
    asset: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_price: float
    confidence: float = 1.0  # déterministe par construction — voir docs/HYPOTHESES.md
    tp1: Optional[float] = None  # ajout 23/08/2026 (voir docs/DECISIONS.md, sortie à prise de profit
    tp2: Optional[float] = None  # H2/H3) — None pour H1 (jamais construit avec ces champs, trailing pur
                                  # inchangé), renseignés par hypothesis2_strategy.py/hypothesis3_strategy.py
                                  # via dataclasses.replace() sur le signal retourné par evaluate_entry ici,
                                  # jamais par ce module lui-même


def compute_regime(candles: List[Candle]) -> Optional[str]:
    """Régime de fond (§2.11, niveau 1) : "long" si la clôture courante
    est au-dessus de la MA(200), "short" si en dessous. None si pas
    assez d'historique, ou égalité stricte (cas dégénéré : aucun régime
    ne doit être présumé)."""
    if len(candles) < MA_PERIOD:
        return None
    ma = sum(c.close for c in candles[-MA_PERIOD:]) / MA_PERIOD
    current_close = candles[-1].close
    if current_close > ma:
        return "long"
    if current_close < ma:
        return "short"
    return None


def compute_donchian_channel(candles: List[Candle], period: int = DONCHIAN_PERIOD) -> Optional[Tuple[float, float]]:
    """Canal de Donchian (plus haut, plus bas) sur `period` bougies,
    EXCLUANT la bougie courante (candles[-1]) : le canal doit représenter
    l'historique précédant le prix testé pour la rupture, sinon la
    bougie courante se compare à elle-même et ne peut jamais "casser" le
    canal. Retourne (plus_haut, plus_bas), ou None si pas assez
    d'historique."""
    if len(candles) < period + 1:
        return None
    window = candles[-(period + 1):-1]
    return max(c.high for c in window), min(c.low for c in window)


def evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    """Point d'entrée unique (§2.11) : régime MA(200) PUIS rupture de
    canal de Donchian(20) dans le sens du régime. Le stop initial est
    placé sur la borne opposée du même canal — aucun paramètre
    supplémentaire pour le stop (voir docs/HYPOTHESES.md).

    Ne lève jamais d'exception : toute erreur interne devient "pas de
    signal" plutôt qu'un signal potentiellement invalide (fail-safe,
    invariant #7 — même patron que risk_engine/validator)."""
    try:
        return _evaluate_entry(asset, candles)
    except Exception:
        return None


def _evaluate_entry(asset: str, candles: List[Candle]) -> Optional[TrendSignal]:
    regime = compute_regime(candles)
    if regime is None:
        return None

    channel = compute_donchian_channel(candles)
    if channel is None:
        return None
    highest, lowest = channel

    current_close = candles[-1].close

    if regime == "long" and current_close > highest:
        return TrendSignal(asset=asset, direction="long", entry_price=current_close, stop_price=lowest)

    if regime == "short" and current_close < lowest:
        return TrendSignal(asset=asset, direction="short", entry_price=current_close, stop_price=highest)

    return None


def compute_tp_levels(
    direction: str, entry_price: float, stop_price: float,
    tp1_r_multiple: float, tp2_r_multiple: float,
) -> Tuple[float, float]:
    """Calcule TP1/TP2 en multiples du risque initial R = |entry_price -
    stop_price| (ajout 23/08/2026, voir docs/DECISIONS.md — sortie à
    prise de profit des Hypothèses #2/#3, mécanisme §2.10 déjà câblé pour
    Station X/H5). Pure arithmétique, aucun état, réutilisée par
    hypothesis2_strategy.py/hypothesis3_strategy.py (et logiquement
    identique à hypothesis5_strategy._compute_tp_levels, laissée
    intacte — voir sa docstring — pour ne courir aucun risque de
    régression sur un module déjà déployé)."""
    r = abs(entry_price - stop_price)
    if direction == "long":
        return entry_price + tp1_r_multiple * r, entry_price + tp2_r_multiple * r
    if direction == "short":
        return entry_price - tp1_r_multiple * r, entry_price - tp2_r_multiple * r
    raise ValueError(f"direction inconnue : {direction!r}")


def compute_trailing_stop_channel(
    direction: str, candles: List[Candle], current_stop: float, period: int = DONCHIAN_PERIOD,
) -> float:
    """Stop suiveur de l'Hypothèse #1 (entrée du 20/08/2026,
    docs/HYPOTHESES.md) : la MÊME fenêtre de Donchian(N=20) qui fixe le
    stop initial (§ evaluate_entry) sert aussi de stop suiveur, recalculée
    à chaque cycle — aucun paramètre supplémentaire (le canal bouge déjà
    avec le prix, pas besoin d'ATR ni d'un second système).

    Applique elle-même le resserrement-seul (invariant #5) par
    max/min — l'appelant (executor.py) fait aussi passer le résultat par
    risk_engine.evaluate_stop_update comme pour le trailing ATR de Station
    X, en deuxième ceinture, jamais en unique garde-fou.

    Si l'historique est insuffisant pour recalculer le canal, retourne le
    stop actuel inchangé (fail-safe, invariant #7 — pas de dégradation
    silencieuse du stop en l'absence de données)."""
    channel = compute_donchian_channel(candles, period)
    if channel is None:
        return current_stop
    highest, lowest = channel
    if direction == "long":
        return max(current_stop, lowest)
    if direction == "short":
        return min(current_stop, highest)
    raise ValueError(f"direction inconnue : {direction!r}")
