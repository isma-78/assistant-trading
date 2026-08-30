"""
backtest_engine.py — Rejeu rétrospectif déterministe des 5 hypothèses
(§2.11 du CDC), sur historique Capital.com persisté localement. MODULE
CRITIQUE (même exigence de couverture que risk_engine.py — un mauvais
calcul ici pourrait influencer une vraie décision live via le garde-fou
Option B d'`executor.open_signal`).

Pré-enregistrement complet de la méthodologie (fenêtre glissante,
convention intra-bougie, modèle de coûts, budget de variables) dans
`docs/HYPOTHESES.md` (24/08/2026, soir) — ÉCRIT avant ce module, pas
après. Ce fichier implémente cette méthodologie telle que pré-enregistrée,
il ne la définit pas a posteriori.

Principe directeur : AUCUNE logique de décision n'est réimplémentée ici.
`evaluate_entry` de chaque hypothèse (inchangée), `executor.decide_entry`,
`executor.evaluate_position_management`, `risk_engine.compute_r_multiple`/
`compute_weighted_r_multiple`, `capital_manager.CapitalManager`/
`apply_trade_result` sont tous réutilisés tels quels — ce module ne fait
que les appeler dans une boucle pilotée par de l'historique au lieu
d'appels broker en direct, et ajoute le modèle de coûts (§2.6) autour
des prix qu'il leur transmet.

Aucun LLM (invariant #1). Fail-safe (invariant #7) : une bougie
historique incomplète (bid/ask manquant) est ignorée, jamais devinée.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from src.capital_manager import CapitalManager, apply_trade_result
from src.executor import (
    ManagementActionType,
    OpenTradeState,
    decide_entry,
    evaluate_position_management,
)
from src.market_data import Candle, compute_atr
from src.risk_engine import RiskEngine, compute_r_multiple, compute_weighted_r_multiple
from src.technical_strategy_executor import _should_refresh_regime_context
from src.regime_confirmation import derive_confirmed_regime

# --- Modèle de coûts (§2.6) — constantes a priori, voir docs/HYPOTHESES.md ---
# Choisies AVANT tout calcul de backtest, jamais ajustées sur un résultat
# (invariant #10). Slippage = multiplicateur du spread réellement observé
# à l'instant de la transaction (1.0 = double le coût de franchissement
# réel) ; financement = taux fixe par jour civil complet au-delà du jour
# d'ouverture, toujours un coût, jamais un crédit.
SLIPPAGE_SPREAD_MULTIPLIER = 1.0
FINANCING_BPS_PER_DAY = 1.0

# Fenêtre glissante fournie à `entry_fn`/aux indices de confirmation —
# IDENTIQUE à `technical_strategy_executor.CANDLE_COUNT` (220) : le
# backtest ne doit jamais montrer plus (ni moins) d'historique que ce que
# le live fournit réellement à chaque cycle (fidélité, pas seulement
# absence d'anticipation).
DEFAULT_LOOKBACK = 220


# ---------------------------------------------------------------------------
# Bougie historique (bid/ask conservés, contrairement à market_data.Candle)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HistoricalBar:
    time_utc: str
    open_bid: float
    open_ask: float
    high_bid: float
    high_ask: float
    low_bid: float
    low_ask: float
    close_bid: float
    close_ask: float
    # `volume` (29/08/2026, voir docs/DECISIONS.md, refonte H1-H5 point C)
    # : même champ `lastTradedVolume` que market_data.Candle, optionnel,
    # rétro-compatible.
    volume: Optional[float] = None

    @property
    def spread_open(self) -> float:
        return round(self.open_ask - self.open_bid, 8)

    def to_candle(self) -> Candle:
        """Mid-price à chaque OHLC, même formule que market_data._mid_of
        (round 8 décimales) — pour rester comparable bit à bit à ce que
        get_candles() aurait produit en direct."""
        return Candle(
            time_utc=self.time_utc,
            open=round((self.open_bid + self.open_ask) / 2, 8),
            high=round((self.high_bid + self.high_ask) / 2, 8),
            low=round((self.low_bid + self.low_ask) / 2, 8),
            close=round((self.close_bid + self.close_ask) / 2, 8),
            volume=self.volume,
        )


def bar_from_raw(raw: dict) -> Optional[HistoricalBar]:
    """Construit une HistoricalBar depuis un point brut de la réponse
    Capital.com (`resp["prices"][i]`, voir capital_client.get_prices).
    None si un bid/ask est manquant sur un des 4 OHLC — fail-safe,
    jamais une bougie devinée."""
    try:
        o, h, l, c = raw["openPrice"], raw["highPrice"], raw["lowPrice"], raw["closePrice"]
        return HistoricalBar(
            time_utc=raw.get("snapshotTimeUTC") or raw.get("snapshotTime", ""),
            open_bid=o["bid"], open_ask=o["ask"],
            high_bid=h["bid"], high_ask=h["ask"],
            low_bid=l["bid"], low_ask=l["ask"],
            close_bid=c["bid"], close_ask=c["ask"],
            volume=raw.get("lastTradedVolume"),
        )
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Modèle de coûts — voir docs/HYPOTHESES.md pour la justification
# ---------------------------------------------------------------------------

def entry_execution_price(direction: str, bar: HistoricalBar) -> float:
    """Prix d'entrée simulé = ouverture de `bar` (la bougie SUIVANT celle
    qui a déclenché le signal, décision pré-enregistrée), payé au bid/ask
    réel (pas le mid) — SANS slippage supplémentaire.

    Corrigé le 26/08/2026 (voir docs/DECISIONS.md, recalibration §2.6) :
    l'entrée est un ordre LIMITE (§2.8, `executor.open_signal` ->
    `client.place_limit_order`), jamais un ordre marché — un ordre
    limite ne peut, par construction, s'exécuter plus mal que son prix.
    Mesuré sur 47 remplissages réels (compte démo) : 0 remplissage pire
    que le prix limite demandé, 36 meilleurs, 11 exacts — aucun crédit
    pris pour le favorable (règle de décision pré-enregistrée,
    conservatrice), mais le terme de slippage défavorable, lui,
    n'a plus de justification et est retiré. Ancien paramètre
    `slippage_multiplier` supprimé (n'a plus de sens ici) — reste
    significatif uniquement sur `exit_execution_price` (sorties TP/stop
    non garanti, ordres MARCHÉ réels via `client.close_position`,
    jamais des ordres limite posés chez le broker, voir docstring de
    cette fonction)."""
    if direction == "long":
        return round(bar.open_ask, 8)
    if direction == "short":
        return round(bar.open_bid, 8)
    raise ValueError(f"direction inconnue : {direction!r}")


def exit_execution_price(
    direction: str, level_price: float, exit_bar_spread: float,
    slippage_multiplier: float = SLIPPAGE_SPREAD_MULTIPLIER,
) -> float:
    """Prix de sortie simulé à un niveau théorique `level_price` (stop/TP,
    valeur mid — calculée par une stratégie sur des Candle en mid), ajusté
    par le demi-spread réel de la bougie de sortie (franchissement bid/ask
    autour du niveau) PLUS slippage forfaitaire — toujours défavorable.

    INCHANGÉ par la recalibration du 26/08/2026 (voir docs/DECISIONS.md) :
    contrairement à l'entrée, TP et stop non garanti sont RÉELLEMENT des
    ordres marché en direct (`executor._apply_management_action` ->
    `client.close_position`, jamais un ordre limite/TP posé à l'avance
    chez le broker — vérifié dans le code, pas supposé). Le prix de
    sortie réel n'est de plus jamais capturé en base
    (`trade_partials.prix_sortie` = valeur théorique, la réponse de
    `close_position` n'est pas lue) : aucune mesure empirique possible
    sur cette jambe, ni en démo ni en réel — le terme de slippage est
    donc conservé tel quel, l'absence de mesure n'étant jamais un motif
    pour l'alléger."""
    slippage = exit_bar_spread * slippage_multiplier
    half_spread = exit_bar_spread / 2
    if direction == "long":  # sortie = vente
        return round(level_price - half_spread - slippage, 8)
    if direction == "short":  # sortie = achat
        return round(level_price + half_spread + slippage, 8)
    raise ValueError(f"direction inconnue : {direction!r}")


def _parse_date(time_utc: str) -> Optional[date]:
    try:
        return date.fromisoformat(time_utc[:10])
    except (ValueError, TypeError):
        return None


def financing_adjusted_exit_price(
    direction: str, exit_price: float, entry_price_for_financing_basis: float,
    entry_date: Optional[date], exit_date: Optional[date],
) -> float:
    """Applique le financement overnight (§2.6) : `FINANCING_BPS_PER_DAY`
    du prix d'entrée, par jour civil complet entre l'entrée et cette
    sortie — TOUJOURS un coût (jamais un crédit), quelle que soit la
    direction (approximation délibérément pessimiste, voir
    docs/HYPOTHESES.md). Aucun ajustement si les dates sont indisponibles
    ou si la sortie a lieu le jour même de l'entrée."""
    if entry_date is None or exit_date is None:
        return exit_price
    days_held = (exit_date - entry_date).days
    if days_held <= 0:
        return exit_price
    financing_cost = entry_price_for_financing_basis * (FINANCING_BPS_PER_DAY / 10000.0) * days_held
    if direction == "long":
        return round(exit_price - financing_cost, 8)
    if direction == "short":
        return round(exit_price + financing_cost, 8)
    raise ValueError(f"direction inconnue : {direction!r}")


# ---------------------------------------------------------------------------
# Résultat d'un trade simulé
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BacktestTrade:
    asset: str
    direction: str
    signal_time_utc: str
    entry_time_utc: str
    exit_time_utc: str
    entry_price_signal: float
    stop_price_signal: float
    entry_price_executed: float
    tp1: Optional[float]
    tp2: Optional[float]
    take_profit: Optional[float]
    units: float
    risk_amount_eur: float
    r_multiple_total: float
    pnl_eur: float
    exit_reason: str
    bid_at_signal: float
    ask_at_signal: float
    spread_at_signal: float


@dataclass(frozen=True)
class BacktestResult:
    trades: List[BacktestTrade]
    final_envelope_balance: float
    final_simulated_reserve: float  # JAMAIS écrit dans reserve_ledger (voir docs/HYPOTHESES.md)


# ---------------------------------------------------------------------------
# Rejeu — un seul actif, une seule hypothèse, à la fois
# ---------------------------------------------------------------------------

def _historical_regime(
    us30_window: List[Candle], us100_window: List[Candle], asset: str,
) -> Optional[str]:
    """Équivalent historique de regime_confirmation.compute_index_regimes
    + derive_confirmed_regime — pas d'appel réseau (les deux séries sont
    déjà en mémoire, chargées depuis l'historique persisté), donc pas de
    retry nécessaire ici (contrairement à la version live, voir
    src/retry.py). Réutilise trend_strategy.compute_regime (MA200) et
    derive_confirmed_regime tels quels — même calcul, autre source de
    bougies."""
    from src.trend_strategy import compute_regime

    index_regimes = {
        "US30": compute_regime(us30_window),
        "US100": compute_regime(us100_window),
    }
    return derive_confirmed_regime(asset, index_regimes)


def _bar_close_time(time_utc: str, duration_seconds: float) -> datetime:
    """Horodatage de CLÔTURE d'une bougie — `time_utc` (convention
    Capital.com `snapshotTimeUTC`) est l'horodatage d'OUVERTURE, jamais
    de clôture. Nécessaire depuis le correctif du 30/08/2026 (voir
    docs/DECISIONS.md, audit lookahead multi-timeframe H2) :
    `_advance_confirming_pointer` comparait auparavant l'OUVERTURE de la
    bougie candidate à `as_of_time`, jamais sa clôture — une bougie tout
    juste ouverte (donc encore EN COURS de formation, high/low/close
    inconnus) passait le test à tort dès que son ouverture précédait
    `as_of_time`, même de quelques secondes."""
    return datetime.fromisoformat(time_utc) + timedelta(seconds=duration_seconds)


def _advance_confirming_pointer(
    series: List[Candle], pointer: int, as_of_time: str, bar_duration_seconds: float,
) -> int:
    """Avance `pointer` (jamais en arrière) jusqu'à l'index de la
    première bougie de `series` dont la CLÔTURE (`time_utc +
    bar_duration_seconds`) est postérieure à `as_of_time` — c'est-à-dire
    jusqu'à inclure toutes les bougies déjà RÉELLEMENT closes à
    `as_of_time`, jamais une bougie encore en cours de formation.

    **Bug réel corrigé le 30/08/2026** (voir docs/DECISIONS.md, audit
    lookahead multi-timeframe demandé par Ismaël sur la confluence H2) :
    la version précédente comparait l'OUVERTURE de la bougie candidate
    à `as_of_time` (`series[pointer].time_utc <= as_of_time`). Pour une
    résolution supérieure à celle de la série appelante (ex. HOUR_4/DAY
    pour une hypothèse en HOUR), une bougie HOUR_4 ouverte à 12h00 reste
    EN COURS jusqu'à 16h00 — l'ancienne condition l'incluait dès 12h00,
    donnant accès à son close final (connu dans l'historique téléchargé
    a posteriori) alors qu'il n'existait pas encore à l'instant simulé.
    Reproduit et corrigé, voir `tests/test_backtest_engine.py`,
    `test_advance_confirming_pointer_excludes_bar_not_yet_closed_at_higher_resolution`.

    Correctif du 25/08/2026 conservé : `own_bars` et les séries de
    confirmation n'ont PAS le même nombre de bougies en pratique (heures
    de marché différentes par instrument) — aligner par position dans
    la liste comparait silencieusement des bougies d'instants
    différents. Les deux séries étant chronologiques par construction,
    ce pointeur monotone donne un résultat correct en O(n) amorti sur
    toute la boucle appelante, sans recherche répétée depuis le début."""
    as_of_dt = datetime.fromisoformat(as_of_time)
    while pointer < len(series) and _bar_close_time(series[pointer].time_utc, bar_duration_seconds) <= as_of_dt:
        pointer += 1
    return pointer


def _trailing_window(series: List[Candle], pointer: int, lookback: int) -> List[Candle]:
    """Les `lookback` dernières bougies de `series` strictement avant
    `pointer` (voir _advance_confirming_pointer) — même contrat que la
    fenêtre glissante de l'actif lui-même (jamais plus que `lookback`
    bougies, jamais une bougie non close à l'instant considéré)."""
    return series[max(0, pointer - lookback):pointer]


def replay_hypothesis(
    asset: str,
    own_bars: List[HistoricalBar],
    entry_fn: Callable[[str, List[Candle]], Optional[object]],
    risk_engine: RiskEngine,
    whitelist: dict,
    envelope_initial: float,
    confidence_threshold: float,
    *,
    require_regime_confirmation: bool = False,
    confirming_bars: Optional[Dict[str, List[HistoricalBar]]] = None,
    is_donchian_trailing: bool = False,
    lookback: int = DEFAULT_LOOKBACK,
    slippage_multiplier: float = SLIPPAGE_SPREAD_MULTIPLIER,
    extra_resolution_bars: Optional[Dict[str, List[HistoricalBar]]] = None,
    own_bar_duration_seconds: Optional[float] = None,
    confirming_bar_duration_seconds: Optional[float] = None,
    extra_resolution_seconds: Optional[Dict[str, float]] = None,
) -> BacktestResult:
    """Rejoue `entry_fn` sur `own_bars` (ordre chronologique), fenêtre
    glissante stricte de `lookback` bougies (jamais de bougie future),
    un signal/trade actif à la fois (même contrat que
    technical_strategy_executor._has_active_signal_or_trade).

    `require_regime_confirmation`/`confirming_bars` : H3/H4 uniquement —
    `confirming_bars` doit contenir les clés "US30"/"US100", mêmes
    longueurs/alignement temporel que `own_bars` (une bougie par index,
    même résolution). Le cache de régime est rafraîchi exactement aux
    mêmes 3 heures UTC fixes que le live (`_should_refresh_regime_context`,
    réutilisée telle quelle), jamais à chaque bougie.

    `is_donchian_trailing` : H1 uniquement — transmet la fenêtre de
    bougies à `evaluate_position_management` pour son trailing Donchian
    pur (état sans tp1/tp2).

    `slippage_multiplier` (défaut = `SLIPPAGE_SPREAD_MULTIPLIER`,
    24/08/2026, voir docs/DECISIONS.md) : permet de rejouer la même
    hypothèse avec un coût de slippage différent (ex. comparaison 100%
    vs 50% du spread), sans changer la constante par défaut.

    `extra_resolution_bars` (29/08/2026, voir docs/DECISIONS.md, point
    16 — H2/L2 uniquement) : résolutions supplémentaires du MÊME actif
    (clés = libellés arbitraires, ex. `{"HOUR_4": bars, "DAY": bars}`),
    alignées par horodatage (même mécanisme que `confirming_bars`,
    jamais par position). Quand fourni, `entry_fn` est appelé avec ces
    fenêtres en arguments positionnels supplémentaires
    (`entry_fn(asset, window, *extra_windows)`, dans l'ORDRE
    d'insertion du dict — nécessaire pour `hypothesis2_strategy_v2.
    evaluate_entry(asset, candles_m15, candles_h1, candles_h4)`, dont
    le contrat à 3 résolutions dépasse le contrat générique à une seule
    résolution). `None` par défaut : H1/H3/H4/H5 strictement
    inchangées (un seul argument à `entry_fn`, comme avant).

    `own_bar_duration_seconds`/`confirming_bar_duration_seconds`/
    `extra_resolution_seconds` (30/08/2026, voir docs/DECISIONS.md,
    audit lookahead multi-timeframe H2) : durée réelle d'une bougie de
    `own_bars`/`confirming_bars`/chaque série de `extra_resolution_bars`
    (secondes) — OBLIGATOIRE dès que `confirming_bars` ou
    `extra_resolution_bars` est fourni (`ValueError` sinon, jamais un
    défaut silencieux qui réintroduirait le bug corrigé). Sert à
    calculer l'instant de clôture RÉEL de la bougie `own_bars[t]`
    (`t.time_utc` est son OUVERTURE, pas sa clôture) avant de
    sélectionner les bougies de confirmation/résolutions supplémentaires
    déjà closes à cet instant — jamais une bougie encore en cours de
    formation à une résolution supérieure (ex. HOUR_4/DAY pour une
    hypothèse en HOUR)."""
    if confirming_bars and confirming_bar_duration_seconds is None:
        raise ValueError(
            "confirming_bar_duration_seconds est obligatoire dès que confirming_bars est fourni "
            "(voir docs/DECISIONS.md, correctif du 30/08/2026 — jamais de défaut silencieux ici)."
        )
    if extra_resolution_bars:
        if own_bar_duration_seconds is None:
            raise ValueError(
                "own_bar_duration_seconds est obligatoire dès que extra_resolution_bars est fourni "
                "(voir docs/DECISIONS.md, correctif du 30/08/2026)."
            )
        missing = set(extra_resolution_bars) - set(extra_resolution_seconds or {})
        if missing:
            raise ValueError(
                f"extra_resolution_seconds doit couvrir toutes les clés de extra_resolution_bars "
                f"(manquant : {sorted(missing)})."
            )

    own_candles = [b.to_candle() for b in own_bars]
    confirming_candles: Dict[str, List[Candle]] = {}
    if require_regime_confirmation and confirming_bars:
        confirming_candles = {k: [b.to_candle() for b in v] for k, v in confirming_bars.items()}
    extra_candles: Dict[str, List[Candle]] = {}
    extra_pointers: Dict[str, int] = {}
    if extra_resolution_bars:
        extra_candles = {k: [b.to_candle() for b in v] for k, v in extra_resolution_bars.items()}
        extra_pointers = {k: 0 for k in extra_candles}
    # Pointeurs monotones PAR HORODATAGE, jamais par position dans la
    # liste (correctif 25/08/2026, voir docs/DECISIONS.md) : own_bars et
    # confirming_bars n'ont PAS le même nombre de bougies en pratique
    # (heures de marché différentes par instrument — ex. EURUSD 24/5 vs
    # US30 24/5 avec des jours fériés propres) ; aligner par index `t`
    # comparait silencieusement des bougies d'instants différents. Les
    # deux séries sont chronologiques (garanti par construction) donc un
    # pointeur qui n'avance jamais en arrière suffit, O(n) amorti.
    confirming_pointers: Dict[str, int] = {k: 0 for k in confirming_candles}

    envelope = CapitalManager(envelope_initial)
    simulated_reserve = 0.0
    trades: List[BacktestTrade] = []

    confirmed_regime: Optional[str] = None
    last_regime_refresh_hour: Optional[int] = None

    open_state: Optional[dict] = None  # voir _open_position/_manage_open_position ci-dessous

    n = len(own_bars)
    for t in range(n):
        window = own_candles[max(0, t + 1 - lookback):t + 1]
        bar = own_bars[t]

        if open_state is not None:
            closed_trade, open_state = _manage_open_position(
                open_state, bar, window, risk_engine, asset, is_donchian_trailing, slippage_multiplier,
            )
            if closed_trade is not None:
                trades.append(closed_trade)
                pnl = closed_trade.pnl_eur
                reserve_share, simulated_reserve = apply_trade_result(
                    envelope, pnl, simulated_reserve, note=f"backtest {asset} {closed_trade.exit_reason}",
                )
            continue

        # Instant de décision RÉEL = clôture de `bar` (son `time_utc` est
        # son OUVERTURE) — correctif du 30/08/2026, voir docs/DECISIONS.md.
        # Sans effet sur `window`/`entry_fn(asset, window)` seul (bar `t`
        # est déjà traitée comme "juste close" par construction de la
        # boucle, voir `execution_bar = own_bars[t + 1]` plus bas) ;
        # nécessaire dès qu'on compare `bar` à une AUTRE série de bougies
        # (confirmation ou résolution supplémentaire), sans quoi une
        # bougie de résolution supérieure encore en cours de formation
        # (ouverte avant la clôture de `bar`, mais pas encore close)
        # serait incluse à tort.
        own_close_time = (
            _bar_close_time(bar.time_utc, own_bar_duration_seconds).isoformat()
            if own_bar_duration_seconds is not None else bar.time_utc
        )

        if require_regime_confirmation:
            hour = _bar_hour(bar.time_utc)
            if hour is not None and _should_refresh_regime_context(last_regime_refresh_hour, hour):
                for key, series in confirming_candles.items():
                    confirming_pointers[key] = _advance_confirming_pointer(
                        series, confirming_pointers[key], own_close_time, confirming_bar_duration_seconds,
                    )
                us30_window = _trailing_window(confirming_candles.get("US30", []), confirming_pointers.get("US30", 0), lookback)
                us100_window = _trailing_window(confirming_candles.get("US100", []), confirming_pointers.get("US100", 0), lookback)
                confirmed_regime = _historical_regime(us30_window, us100_window, asset)
                last_regime_refresh_hour = hour

        if extra_candles:
            extra_windows = []
            for key, series in extra_candles.items():
                extra_pointers[key] = _advance_confirming_pointer(
                    series, extra_pointers[key], own_close_time, extra_resolution_seconds[key],
                )
                extra_windows.append(_trailing_window(series, extra_pointers[key], lookback))
            signal = entry_fn(asset, window, *extra_windows)
        else:
            signal = entry_fn(asset, window)
        if signal is None:
            continue
        if require_regime_confirmation and signal.direction != confirmed_regime:
            continue
        if t + 1 >= n:
            break  # aucune bougie suivante disponible pour exécuter — pas de trade simulé

        execution_bar = own_bars[t + 1]
        execution_mid_open = round((execution_bar.open_bid + execution_bar.open_ask) / 2, 8)

        decision = decide_entry(
            asset=asset, direction=signal.direction, entry_price=signal.entry_price,
            stop_price=signal.stop_price, confidence=getattr(signal, "confidence", 1.0),
            current_price=execution_mid_open, market_status="TRADEABLE",
            risk_engine=risk_engine, whitelist=whitelist, envelope_balance=envelope.balance,
            confidence_threshold=confidence_threshold, go_nogo_ok=True,
        )
        if not decision.approved:
            continue

        executed_entry_price = entry_execution_price(signal.direction, execution_bar)
        entry_date = _parse_date(execution_bar.time_utc)

        state = OpenTradeState(
            trade_id=-1, deal_id="backtest", asset=asset, source="backtest",
            direction=signal.direction, entry_price=executed_entry_price,
            initial_stop_price=signal.stop_price, stop_price=signal.stop_price,
            tp1=getattr(signal, "tp1", None), tp2=getattr(signal, "tp2", None),
            tp1_hit=False, tp2_hit=False, remaining_fraction=1.0, guaranteed_stop=False,
            take_profit=getattr(signal, "take_profit", None),
        )
        open_state = {
            "state": state,
            "units": decision.risk_decision.units,
            "risk_amount_eur": decision.risk_decision.risk_amount_eur,
            "signal_time_utc": bar.time_utc,
            "entry_time_utc": execution_bar.time_utc,
            "entry_date": entry_date,
            "partials": [],  # List[Tuple[fraction, r_multiple]]
            "bid_at_signal": bar.close_bid,
            "ask_at_signal": bar.close_ask,
        }

    return BacktestResult(trades=trades, final_envelope_balance=envelope.balance, final_simulated_reserve=simulated_reserve)


def _bar_hour(time_utc: str) -> Optional[int]:
    try:
        return int(time_utc[11:13])
    except (ValueError, TypeError):
        return None


def _manage_open_position(
    open_state: dict, bar: HistoricalBar, window: List[Candle], risk_engine: RiskEngine,
    asset: str, is_donchian_trailing: bool, slippage_multiplier: float = SLIPPAGE_SPREAD_MULTIPLIER,
) -> Tuple[Optional[BacktestTrade], Optional[dict]]:
    """Gère une position simulée sur `bar` : stop testé EN PREMIER au
    point le plus défavorable de la bougie, cible/trailing testés ensuite
    au point le plus favorable (convention pessimiste pré-enregistrée,
    voir docs/HYPOTHESES.md). Retourne (trade fermé ou None, nouvel état
    ou None si fermé)."""
    state: OpenTradeState = open_state["state"]
    candle = bar.to_candle()
    atr = compute_atr(window, period=14)
    trailing_candles = window if is_donchian_trailing else None

    worst_price = candle.low if state.direction == "long" else candle.high
    action = evaluate_position_management(state, worst_price, atr, risk_engine, trailing_candles)

    if action.action == ManagementActionType.NONE:
        best_price = candle.high if state.direction == "long" else candle.low
        action = evaluate_position_management(state, best_price, atr, risk_engine, trailing_candles)

    if action.action == ManagementActionType.UPDATE_TRAILING_STOP:
        new_state = OpenTradeState(
            trade_id=state.trade_id, deal_id=state.deal_id, asset=state.asset, source=state.source,
            direction=state.direction, entry_price=state.entry_price, initial_stop_price=state.initial_stop_price,
            stop_price=action.new_stop_price, tp1=state.tp1, tp2=state.tp2, tp1_hit=state.tp1_hit,
            tp2_hit=state.tp2_hit, remaining_fraction=state.remaining_fraction,
            guaranteed_stop=state.guaranteed_stop, take_profit=state.take_profit,
        )
        open_state = dict(open_state, state=new_state)
        return None, open_state

    if action.action == ManagementActionType.NONE:
        return None, open_state

    exit_date = _parse_date(bar.time_utc)
    executed_exit_price = exit_execution_price(state.direction, action.exit_price, bar.spread_open, slippage_multiplier)
    executed_exit_price = financing_adjusted_exit_price(
        state.direction, executed_exit_price, state.entry_price, open_state["entry_date"], exit_date,
    )
    r_this_leg = compute_r_multiple(state.direction, state.entry_price, state.initial_stop_price, executed_exit_price)
    partials = list(open_state["partials"]) + [(action.fraction_to_close, r_this_leg)]

    if action.action == ManagementActionType.CLOSE_PARTIAL_TP1:
        new_state = OpenTradeState(
            trade_id=state.trade_id, deal_id=state.deal_id, asset=state.asset, source=state.source,
            direction=state.direction, entry_price=state.entry_price, initial_stop_price=state.initial_stop_price,
            stop_price=action.new_stop_price if action.new_stop_price is not None else state.stop_price,
            tp1=state.tp1, tp2=state.tp2, tp1_hit=True, tp2_hit=state.tp2_hit,
            remaining_fraction=round(state.remaining_fraction - action.fraction_to_close, 10),
            guaranteed_stop=state.guaranteed_stop, take_profit=state.take_profit,
        )
        open_state = dict(open_state, state=new_state, partials=partials)
        return None, open_state

    if action.action == ManagementActionType.CLOSE_PARTIAL_TP2:
        new_state = OpenTradeState(
            trade_id=state.trade_id, deal_id=state.deal_id, asset=state.asset, source=state.source,
            direction=state.direction, entry_price=state.entry_price, initial_stop_price=state.initial_stop_price,
            stop_price=state.stop_price, tp1=state.tp1, tp2=state.tp2, tp1_hit=state.tp1_hit, tp2_hit=True,
            remaining_fraction=round(state.remaining_fraction - action.fraction_to_close, 10),
            guaranteed_stop=state.guaranteed_stop, take_profit=state.take_profit,
        )
        open_state = dict(open_state, state=new_state, partials=partials)
        return None, open_state

    # CLOSE_FULL_STOP ou CLOSE_FULL_TP : position entièrement fermée.
    r_total = compute_weighted_r_multiple(partials)
    risk_amount_eur = open_state["risk_amount_eur"]
    pnl_eur = round(r_total * risk_amount_eur, 2)
    trade = BacktestTrade(
        asset=asset, direction=state.direction,
        signal_time_utc=open_state["signal_time_utc"], entry_time_utc=open_state["entry_time_utc"],
        exit_time_utc=bar.time_utc, entry_price_signal=state.entry_price, stop_price_signal=state.initial_stop_price,
        entry_price_executed=state.entry_price, tp1=state.tp1, tp2=state.tp2, take_profit=state.take_profit,
        units=open_state["units"], risk_amount_eur=risk_amount_eur, r_multiple_total=r_total, pnl_eur=pnl_eur,
        exit_reason=action.action.value,
        bid_at_signal=open_state["bid_at_signal"], ask_at_signal=open_state["ask_at_signal"],
        spread_at_signal=round(open_state["ask_at_signal"] - open_state["bid_at_signal"], 8),
    )
    return trade, None
