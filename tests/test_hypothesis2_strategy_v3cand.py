"""
tests/test_hypothesis2_strategy_v3cand.py — Candidate V2 pour H2/L2
(06/10/2026, voir docs/PROTOCOLE_EVOLUTION_V2_06-10.md). Le test
anti-lookahead prouve qu'un vote H1/H4 qui ne bascule qu'ENTRE le point de
contrôle `t-k` et l'instant courant `t` ne fait jamais paraître `t-k`
comme déjà aligné (ce qui masquerait à tort une transition réelle).
"""

import pytest

from src.hypothesis2_strategy_v2 import N_TF
from src.hypothesis2_strategy_v3cand import (
    TRANSITION_LOOKBACK_CANDLES,
    _truncate_by_time,
    evaluate_entry,
    was_already_aligned,
)
from src.market_data import Candle
from src.trend_strategy import TrendSignal


def _t(i):
    # ISO-like, zéro-paddé : comparaison lexicographique = comparaison chronologique.
    return f"2026-01-01T{i:04d}:00:00"


def _c(i, high, low, close):
    return Candle(time_utc=_t(i), open=close, high=high, low=low, close=close)


def _trending_series(n, start=100.0, step=1.0, offset=0):
    candles = []
    price = start
    for i in range(n):
        price += step
        candles.append(_c(offset + i, price + 1, price - 1, price))
    return candles


def _long_ready_series(n=130, offset=0):
    return _trending_series(n, start=100.0, step=1.0, offset=offset)


def _flat_series(n, offset=0, level=100.0):
    return [_c(offset + i, level + 1, level - 1, level) for i in range(n)]


# ---------------------------------------------------------------------------
# _truncate_by_time
# ---------------------------------------------------------------------------

def test_truncate_by_time_keeps_only_bars_up_to_cutoff():
    candles = [_c(i, 101, 99, 100) for i in range(5)]
    truncated = _truncate_by_time(candles, _t(2))
    assert [c.time_utc for c in truncated] == [_t(0), _t(1), _t(2)]
    assert _truncate_by_time(candles, _t(0)) == [candles[0]]


# ---------------------------------------------------------------------------
# was_already_aligned — y compris anti-lookahead
# ---------------------------------------------------------------------------

def test_was_already_aligned_true_when_confluence_already_established():
    m15 = _long_ready_series(130)
    h1 = _long_ready_series(130)
    h4 = _long_ready_series(130)
    # À un index bien avancé de la série montante, la confluence est déjà établie.
    assert was_already_aligned(m15, h1, h4, "long", 125) is True


def test_was_already_aligned_none_when_h1_h4_history_insufficient_at_checkpoint():
    """H1/H4 ne démarrent (dans le temps) qu'APRÈS un certain point : à un
    index M15 antérieur à leur toute première bougie, la troncature par
    horodatage ne laisse plus aucune bougie H1/H4 -> indéterminable."""
    m15 = _long_ready_series(130)
    h1 = _long_ready_series(80, offset=50)
    h4 = _long_ready_series(80, offset=50)
    assert was_already_aligned(m15, h1, h4, "long", 10) is None
    assert was_already_aligned(m15, h1, h4, "long", -1) is None
    assert was_already_aligned(m15, h1, h4, "long", 999) is None


def test_was_already_aligned_uses_h1_h4_state_as_of_the_checkpoint_not_the_future():
    """Construit un H1 dont le vote ne devient `long` qu'APRÈS le point de
    contrôle (basculement tardif) : le vérifier à `t-k` doit renvoyer
    False, jamais True sous prétexte que le H1 FOURNI (complet, incluant
    le futur) est déjà aligné en fin de série."""
    m15 = _long_ready_series(130)
    checkpoint = 100
    # H4 JAMAIS aligné (plat tout du long) : avec N_TF=2, seul H1 peut
    # faire basculer le compte à 2 (m15 + h1) — isole complètement
    # l'effet de la troncature de H1 sur le résultat.
    h4 = _flat_series(130)
    # H1 : flat (pas de vote tranché) jusqu'à `checkpoint`, puis tendance
    # nette ENSUITE seulement — son vote `long` ne peut se former qu'après.
    h1 = _flat_series(checkpoint + 1) + _trending_series(130 - checkpoint - 1, start=100.0, offset=checkpoint + 1)

    # Au point de contrôle, H1 tronqué à cet instant n'a vu QUE la partie
    # plate : son vote ne peut pas être "long" -> count=1 (m15 seul) -> pas
    # déjà aligné (N_TF=2 non atteint).
    assert was_already_aligned(m15, h1, h4, "long", checkpoint) is False

    # Preuve directe de la troncature : le H1 COMPLET (non tronqué) est
    # pourtant aligné en fin de série — si le code utilisait `h1` complet
    # au lieu de le tronquer à `cutoff`, il répondrait True à tort.
    from src.hypothesis2_strategy_v2 import compute_tf_vote
    assert compute_tf_vote(h1, len(h1) - 1) == "long"


def test_was_already_aligned_true_for_short_direction():
    down = _trending_series(130, start=200.0, step=-1.0)
    assert was_already_aligned(down, down, down, "short", 125) is True
    assert was_already_aligned(down, down, down, "long", 125) is False


def test_was_already_aligned_false_when_direction_not_yet_reached():
    m15 = _flat_series(130)
    h1 = _flat_series(130)
    h4 = _flat_series(130)
    assert was_already_aligned(m15, h1, h4, "long", 100) is False


# ---------------------------------------------------------------------------
# evaluate_entry
# ---------------------------------------------------------------------------

def test_evaluate_entry_fires_on_newly_established_alignment():
    """Confluence établie seulement sur les toutes dernières bougies
    (flat avant, tendance après, le vote bascule dès la 1ère bougie de
    tendance — vérifié empiriquement) -> transition récente -> signal."""
    flat_len = 125
    trend_len = TRANSITION_LOOKBACK_CANDLES  # le point de contrôle (t-k) retombe pile dans le plat
    m15 = _flat_series(flat_len) + _trending_series(trend_len, start=100.0, offset=flat_len)
    h1 = _flat_series(flat_len) + _trending_series(trend_len, start=100.0, offset=flat_len)
    h4 = _flat_series(flat_len) + _trending_series(trend_len, start=100.0, offset=flat_len)
    assert was_already_aligned(m15, h1, h4, "long", flat_len - 1) is False
    signal = evaluate_entry("EURUSD", m15, h1, h4)
    assert isinstance(signal, TrendSignal)
    assert signal.direction == "long"


def test_evaluate_entry_none_when_alignment_already_established_long_ago():
    """Même confluence, mais établie depuis longtemps (pas une transition
    récente) -> aucun signal, contrairement à L2/v2 qui aurait signalé ICI
    à chaque cycle (état persistant, 98% des heures)."""
    m15 = _long_ready_series(130)
    h1 = _long_ready_series(130)
    h4 = _long_ready_series(130)
    assert evaluate_entry("EURUSD", m15, h1, h4) is None


def test_evaluate_entry_none_when_no_base_signal():
    flat = _flat_series(130)
    assert evaluate_entry("EURUSD", flat, flat, flat) is None


def test_evaluate_entry_none_when_insufficient_history_for_transition_check(monkeypatch):
    """Si la base de signal fournit un signal alors que l'historique
    disponible est plus court que `TRANSITION_LOOKBACK_CANDLES` (ne se
    produit jamais en pratique avec la base réelle, qui exige bien plus
    de bougies — forcé ici pour couvrir le garde-fou fail-safe)."""
    import src.hypothesis2_strategy_v3cand as mod
    fake_signal = TrendSignal(asset="EURUSD", direction="long", entry_price=1.0, stop_price=0.9, tp1=1.1, tp2=1.2)
    monkeypatch.setattr(mod, "_v2_evaluate_entry", lambda *a, **k: fake_signal)
    short = [_c(i, 1, 1, 1) for i in range(TRANSITION_LOOKBACK_CANDLES)]
    assert evaluate_entry("EURUSD", short, short, short) is None


def test_evaluate_entry_wraps_unexpected_exception_as_no_signal(monkeypatch):
    import src.hypothesis2_strategy_v3cand as mod
    flat_len = 125
    m15 = _flat_series(flat_len) + _trending_series(10, start=100.0, offset=flat_len)
    h1 = _flat_series(flat_len) + _trending_series(10, start=100.0, offset=flat_len)
    h4 = _flat_series(flat_len) + _trending_series(10, start=100.0, offset=flat_len)
    monkeypatch.setattr(mod, "was_already_aligned", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert evaluate_entry("EURUSD", m15, h1, h4) is None


def test_module_constants_within_preregistered_grid():
    from src.hypothesis2_strategy_v3cand import TRANSITION_LOOKBACK_CANDLES_GRID
    assert TRANSITION_LOOKBACK_CANDLES in TRANSITION_LOOKBACK_CANDLES_GRID
    assert len(TRANSITION_LOOKBACK_CANDLES_GRID) == 6
    assert N_TF  # réutilisé tel quel, pas redéfini ici
