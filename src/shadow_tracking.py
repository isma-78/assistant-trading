"""
shadow_tracking.py — Suivi forward en SHADOW des 4 candidates V2
(06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md §6-7). Préparation
pour le redéploiement du 10/10 : ce module n'est exercé en direct par
AUCUN cron avant l'activation explicite décrite dans
`docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md`.

Principe : un signal virtuel n'est JAMAIS un ordre. `run_shadow_cycle`
(orchestration I/O, au bas de ce fichier) n'appelle que des points de
lecture déjà utilisés par les exécuteurs réels
(`CapitalClient.get_prices`/`get_market_snapshot`) — jamais
`place_limit_order`/`open_position`/`close_position`/`update_position_
stop`/`cancel_working_order`. Aucun effet sur `trades`, le plafond de
cluster, le capital ou `risk_engine` : la gestion d'une position virtuelle
réutilise `backtest_engine._manage_open_position` (déjà testé, jamais
dupliqué) sur une bougie SYNTHÉTIQUE construite depuis la dernière bougie
réelle + le spread capturé au moment de la lecture — approximation
documentée : le spread intra-bougie réel (bid/ask par OHLC) n'est pas
observable en direct avec `get_candles` (prix médian uniquement), donc une
bougie synthétique à spread CONSTANT autour du prix médian est utilisée à
la place (`historical_bar_from_candle`).

Chaque source `_v3cand` suit son propre historique, jamais mélangé aux
sources `_v2` ni entre candidates (une table dédiée, `shadow_trades`/
`shadow_partials`/`shadow_epochs`, jamais `trades`/`trade_partials`).
"""

import logging
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional

from src.backtest_engine import HistoricalBar, _manage_open_position
from src.capital_client import CapitalApiError, CapitalClient
from src.db import connection_scope
from src.executor import OpenTradeState
from src.hypothesis1_strategy_v3cand import evaluate_entry as h1_cand_entry
from src.hypothesis2_strategy_v3cand import evaluate_entry as h2_cand_entry
from src.hypothesis3_strategy_v3cand import evaluate_entry as h3_cand_entry
from src.hypothesis4_strategy_v3cand import evaluate_entry as h4_cand_entry
from src.market_data import Candle, get_candles, get_price_snapshot
from src.risk_engine import RiskEngine
from src.simulator_fidelity import StopRefusalModel

logger = logging.getLogger(__name__)
CANDLE_COUNT = 220  # même fenêtre que les exécuteurs réels (technical_strategy_executor.CANDLE_COUNT)
EXTRA_RESOLUTION_CANDLE_COUNT = 100  # même convention que hypothesis2_executor.py

ASSETS_H1 = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "CHFJPY"]
ASSETS_H234 = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]  # CHFJPY exclue (05/10)

# Registre des 4 candidates (source -> config). Jamais mélangé aux
# sources "_v2" ni entre elles (une table dédiée, voir docstring).
SHADOW_CANDIDATES: Dict[str, dict] = {
    "hypothesis_v3cand": {"entry_fn": h1_cand_entry, "assets": ASSETS_H1, "extras": [], "donchian": False},
    "hypothesis2_v3cand": {"entry_fn": h2_cand_entry, "assets": ASSETS_H234, "extras": ["HOUR_4", "DAY"], "donchian": False},
    "hypothesis3_v3cand": {"entry_fn": h3_cand_entry, "assets": ASSETS_H234, "extras": [], "donchian": False},
    "hypothesis4_v3cand": {"entry_fn": h4_cand_entry, "assets": ASSETS_H234, "extras": [], "donchian": False},
}
RESOLUTION = "HOUR"


def historical_bar_from_candle(candle: Candle, spread: float) -> HistoricalBar:
    """Bougie synthétique pour la gestion virtuelle (voir docstring du
    module) : chaque OHLC médian +/- la moitié du spread capturé — jamais
    le spread réel intra-bougie, approximation assumée."""
    half = spread / 2.0
    return HistoricalBar(
        time_utc=candle.time_utc,
        open_bid=candle.open - half, open_ask=candle.open + half,
        high_bid=candle.high - half, high_ask=candle.high + half,
        low_bid=candle.low - half, low_ask=candle.low + half,
        close_bid=candle.close - half, close_ask=candle.close + half,
        volume=candle.volume,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_open_shadow_trade(conn, source: str, asset: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM shadow_trades WHERE source = ? AND actif = ? AND statut = 'ouvert' LIMIT 1", (source, asset),
    ).fetchone()
    return row is not None


def open_shadow_trade(
    db_path: str, source: str, asset: str, signal, bid: float, ask: float, now: Optional[str] = None,
) -> Optional[int]:
    """Ouvre un signal virtuel (table `shadow_trades`) si et seulement si
    aucun n'est déjà ouvert pour (source, actif) — une position virtuelle
    à la fois, même règle que le live. Retourne l'id créé, ou `None` si
    déjà ouvert (jamais un doublon)."""
    now = now or _now_iso()
    with connection_scope(db_path) as conn:
        if has_open_shadow_trade(conn, source, asset):
            return None
        cursor = conn.execute(
            "INSERT INTO shadow_trades (source, actif, direction, entry_price, stop_loss_initial, "
            "stop_loss_courant, tp1, tp2, bid_at_signal, ask_at_signal, ouvert_at, statut, remaining_fraction) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ouvert', 1.0)",
            (source, asset, signal.direction, signal.entry_price, signal.stop_price, signal.stop_price,
             signal.tp1, signal.tp2, bid, ask, now),
        )
        return cursor.lastrowid


def _load_open_state(conn, trade_row) -> dict:
    """`tp1_hit`/`tp2_hit` dérivés des paliers déjà enregistrés — jamais un
    état séparé qui pourrait diverger (même principe que
    `executor._load_open_trade_state`)."""
    partials = conn.execute(
        "SELECT palier, fraction, r_multiple FROM shadow_partials WHERE shadow_trade_id = ? ORDER BY id",
        (trade_row["id"],),
    ).fetchall()
    state = OpenTradeState(
        trade_id=trade_row["id"], deal_id=f"shadow-{trade_row['id']}", asset=trade_row["actif"],
        source=trade_row["source"], direction=trade_row["direction"], entry_price=trade_row["entry_price"],
        initial_stop_price=trade_row["stop_loss_initial"], stop_price=trade_row["stop_loss_courant"],
        tp1=trade_row["tp1"], tp2=trade_row["tp2"],
        tp1_hit=any(p["palier"] == "tp1" for p in partials), tp2_hit=any(p["palier"] == "tp2" for p in partials),
        remaining_fraction=trade_row["remaining_fraction"],
    )
    entry_date = date.fromisoformat(trade_row["ouvert_at"][:10])
    return {
        "state": state, "units": 1.0, "risk_amount_eur": 1.0,  # unités arbitraires : seul le R compte en shadow
        "signal_time_utc": trade_row["ouvert_at"], "entry_time_utc": trade_row["ouvert_at"],
        "entry_date": entry_date, "partials": [(p["fraction"], p["r_multiple"]) for p in partials
                                                if p["palier"] in ("tp1", "tp2")],
        "bid_at_signal": trade_row["bid_at_signal"], "ask_at_signal": trade_row["ask_at_signal"],
    }


def advance_shadow_trade(
    db_path: str, trade_row, bar: HistoricalBar, window: List[Candle], risk_engine, is_donchian_trailing: bool,
    stop_update_filter: Optional[Callable] = None, slippage_multiplier: float = 1.0,
) -> Optional[float]:
    """Avance une position virtuelle d'une bougie (réutilise
    `backtest_engine._manage_open_position`, jamais dupliquée). Retourne
    le R total si la position s'est fermée, `None` si elle reste ouverte
    ou si seul le stop a bougé."""
    with connection_scope(db_path) as conn:
        open_state = _load_open_state(conn, trade_row)
        closed_trade, new_open_state = _manage_open_position(
            open_state, bar, window, risk_engine, trade_row["actif"], is_donchian_trailing, slippage_multiplier,
            stop_update_filter,
        )
        if closed_trade is None and new_open_state is not None:
            conn.execute(
                "UPDATE shadow_trades SET stop_loss_courant = ? WHERE id = ?",
                (new_open_state["state"].stop_price, trade_row["id"]),
            )
            # Un nouveau palier a pu être ajouté (TP1/TP2) sans clôturer le trade.
            if len(new_open_state["partials"]) > len(open_state["partials"]):
                fraction, r = new_open_state["partials"][-1]
                palier = "tp1" if new_open_state["state"].tp1_hit and not open_state["state"].tp1_hit else "tp2"
                conn.execute(
                    "INSERT INTO shadow_partials (shadow_trade_id, palier, fraction, exit_price, r_multiple, exit_time_utc) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (trade_row["id"], palier, fraction, bar.to_candle().close, r, bar.time_utc),
                )
                conn.execute(
                    "UPDATE shadow_trades SET remaining_fraction = ? WHERE id = ?",
                    (new_open_state["state"].remaining_fraction, trade_row["id"]),
                )
            return None
        if closed_trade is not None:
            conn.execute(
                "INSERT INTO shadow_partials (shadow_trade_id, palier, fraction, exit_price, r_multiple, exit_time_utc) "
                "VALUES (?, 'sl_ou_tp_final', ?, ?, ?, ?)",
                (trade_row["id"], 1.0 - sum(f for f, _ in open_state["partials"]),
                 bar.to_candle().close, closed_trade.r_multiple_total, bar.time_utc),
            )
            conn.execute(
                "UPDATE shadow_trades SET statut = 'ferme', ferme_at = ?, r_multiple_total = ?, remaining_fraction = 0.0 "
                "WHERE id = ?",
                (bar.time_utc, closed_trade.r_multiple_total, trade_row["id"]),
            )
            return closed_trade.r_multiple_total
    return None


# ---------------------------------------------------------------------------
# Orchestration I/O — AUCUN ordre, AUCUNE écriture ailleurs que
# shadow_trades/shadow_partials/shadow_epochs. N'est exercé par aucun cron
# avant l'activation explicite (voir docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md).
# ---------------------------------------------------------------------------

def ensure_shadow_epoch(db_path: str, source: str, now: Optional[str] = None) -> None:
    """Écrit T0 pour `source` s'il n'existe pas déjà (jamais réécrit —
    T0 est le premier redémarrage du cycle shadow, jamais rétroactif)."""
    with connection_scope(db_path) as conn:
        existing = conn.execute("SELECT 1 FROM shadow_epochs WHERE source = ?", (source,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO shadow_epochs (source, started_at, description) VALUES (?, ?, ?)",
                (source, now or _now_iso(), "T0 — premier cycle shadow (06/10/2026, préparation 10/10)"),
            )


def _get_candles_safe(client: CapitalClient, epic: str, resolution: str, count: int) -> List[Candle]:
    try:
        return get_candles(client, epic, resolution=resolution, count=count)
    except CapitalApiError:
        logger.exception("Shadow : lecture des bougies %s/%s impossible — cycle sauté pour cet actif", epic, resolution)
        return []


def run_shadow_cycle(
    db_path: str, client: CapitalClient, risk_engine: RiskEngine, stop_refusal_model: Optional[StopRefusalModel] = None,
    candidates: Dict[str, dict] = SHADOW_CANDIDATES,
) -> int:
    """Un cycle de suivi shadow pour les 4 candidates. Lecture seule côté
    broker (`get_candles`/`get_market_snapshot`, déjà utilisés par les
    exécuteurs réels) — AUCUN ordre, jamais. Retourne le nombre de
    positions virtuelles fermées pendant ce cycle. Appelée par
    `scripts/run_shadow_cycle.py` (cron, à activer après le 10/10 — voir
    docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md), jamais en arrière-plan avant."""
    now = _now_iso()
    closed_count = 0
    for source, cfg in candidates.items():
        ensure_shadow_epoch(db_path, source, now)
        for asset in cfg["assets"]:
            own_candles = _get_candles_safe(client, asset, RESOLUTION, CANDLE_COUNT)
            if not own_candles:
                continue
            with connection_scope(db_path) as conn:
                open_row = conn.execute(
                    "SELECT * FROM shadow_trades WHERE source = ? AND actif = ? AND statut = 'ouvert' LIMIT 1",
                    (source, asset),
                ).fetchone()
            if open_row is not None:
                try:
                    snapshot = get_price_snapshot(client, asset)
                except CapitalApiError:
                    logger.exception("Shadow : snapshot indisponible pour %s — gestion sautée ce cycle", asset)
                    continue
                bar = historical_bar_from_candle(own_candles[-1], snapshot.ask - snapshot.bid)
                atr_window = own_candles  # fenêtre complète, cohérent avec compute_atr déjà causal
                r = advance_shadow_trade(
                    db_path, open_row, bar, atr_window, risk_engine, cfg["donchian"], stop_refusal_model,
                )
                if r is not None:
                    closed_count += 1
                continue

            extra_windows = []
            if cfg["extras"]:
                extra_windows = [_get_candles_safe(client, asset, r, EXTRA_RESOLUTION_CANDLE_COUNT) for r in cfg["extras"]]
                if any(not w for w in extra_windows):
                    continue
            signal = cfg["entry_fn"](asset, own_candles, *extra_windows) if extra_windows else cfg["entry_fn"](asset, own_candles)
            if signal is None:
                continue
            try:
                snapshot = get_price_snapshot(client, asset)
            except CapitalApiError:
                logger.exception("Shadow : snapshot indisponible pour %s — signal perdu ce cycle (pas de rattrapage)", asset)
                continue
            open_shadow_trade(db_path, source, asset, signal, snapshot.bid, snapshot.ask, now)
    return closed_count
