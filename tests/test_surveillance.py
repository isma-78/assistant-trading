import importlib.util
import math
import pathlib
import random

import pytest

from src import surveillance as sv
from src.db import connection_scope, get_connection, init_db

_spec = importlib.util.spec_from_file_location(
    "controle", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "controle_surveillance.py")
controle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(controle)


# --- loi de Student ---------------------------------------------------------

@pytest.mark.parametrize("p,df,expected", [
    (0.975, 14, 2.14479), (0.95, 14, 1.76131), (0.975, 29, 2.04523), (0.975, 1, 12.7062), (0.99, 52, 2.4002),
])
def test_student_ppf_matches_reference_tables(p, df, expected):
    assert sv.student_t_ppf(p, df) == pytest.approx(expected, rel=2e-4)


def test_student_cdf_symmetry_and_ppf_inverse():
    assert sv.student_t_cdf(0.0, 10) == pytest.approx(0.5)
    assert sv.student_t_cdf(-1.3, 10) == pytest.approx(1 - sv.student_t_cdf(1.3, 10))
    assert sv.student_t_ppf(0.3, 7) == pytest.approx(-sv.student_t_ppf(0.7, 7))
    assert sv.student_t_ppf(0.5, 7) == 0.0
    assert sv.student_t_cdf(sv.student_t_ppf(0.9, 20), 20) == pytest.approx(0.9)
    assert sv.student_t_cdf(40.0, 5) == pytest.approx(1.0, abs=1e-6)


def test_student_domain_errors():
    with pytest.raises(ValueError):
        sv.student_t_cdf(1.0, 0)
    with pytest.raises(ValueError):
        sv.student_t_ppf(1.0, 5)


# --- statistiques d'échantillon ---------------------------------------------

def test_sample_stats_cases():
    assert sv.sample_stats([]).n == 0 and sv.sample_stats([]).mean is None
    one = sv.sample_stats([0.5])
    assert one.mean == 0.5 and one.ci_low is None and one.p_lower is None
    const_neg = sv.sample_stats([-1.0, -1.0, -1.0])
    assert const_neg.ci_high == -1.0 and const_neg.p_lower == 0.0
    assert sv.sample_stats([1.0, 1.0]).p_lower == 1.0
    values = [-1, -0.5, -2, -1.5, -0.8]
    st = sv.sample_stats(values)
    mean = sum(values) / 5
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / 4)
    half = sv.student_t_ppf(0.975, 4) * sd / math.sqrt(5)
    assert st.ci_high == pytest.approx(mean + half) and st.ci_low == pytest.approx(mean - half)


def test_upper_bound_at_alpha():
    assert sv.upper_bound_at_alpha([1.0], 0.05) is None
    assert sv.upper_bound_at_alpha([-1.0, -1.0], 0.05) == -1.0
    vals = [-1.0, -0.5, -2.0, -1.5]
    assert sv.upper_bound_at_alpha(vals, 0.025) == pytest.approx(sv.sample_stats(vals).ci_high)


# --- règle de suspension ----------------------------------------------------

def _neg(n, mean=-1.0, spread=0.3, seed=1):
    rng = random.Random(seed)
    return [mean + rng.uniform(-spread, spread) for _ in range(n)]


def test_below_min_trades_never_suspended():
    v = sv.evaluate_hypotheses({"H3": [-3.0] * 14})
    assert v["H3"].status == sv.STATUS_INCONCLUSIVE and v["H3"].p_value is None


def test_clearly_negative_with_15_trades_is_suspended_and_bound_below_zero():
    v = sv.evaluate_hypotheses({"H3": _neg(15)})
    assert v["H3"].status == sv.STATUS_SUSPENDED
    assert v["H3"].upper_bound_holm < 0 and v["H3"].p_value <= v["H3"].holm_alpha
    assert all(v[h].status == sv.STATUS_INCONCLUSIVE for h in ("H1", "H2", "H4", "H5"))


def test_ci_containing_zero_stays_active():
    vals = [-0.5, 0.6, -0.2, 0.9, -0.7, 0.4, 0.1, -0.3, 0.8, -0.9, 0.2, 0.5, -0.1, 0.3, -0.4]
    v = sv.evaluate_hypotheses({"H1": vals})
    assert v["H1"].status == sv.STATUS_ACTIVE and v["H1"].upper_bound_holm > 0


def test_holm_is_stricter_than_unadjusted_for_borderline_case():
    # rejetée à 0,05 seule, pas au seuil de Holm (0,01 au rang 0 sur 5)
    vals = [x + 0.04 for x in [-0.35, 0.1, -0.5, 0.2, -0.4, -0.1, 0.0, -0.45, 0.15, -0.3, -0.2, 0.05, -0.25, -0.15, -0.3]]
    st = sv.sample_stats(vals)
    v = sv.evaluate_hypotheses({"H2": vals})
    assert 0.01 < st.p_lower < 0.05
    assert v["H2"].status == sv.STATUS_ACTIVE


def test_bound_below_zero_iff_holm_rejection():
    for seed in range(40):
        data = {"H1": _neg(20, mean=-0.4, spread=0.9, seed=seed), "H4": _neg(16, mean=-0.2, spread=0.9, seed=seed + 99)}
        v = sv.evaluate_hypotheses(data)
        for h in ("H1", "H4"):
            assert (v[h].status == sv.STATUS_SUSPENDED) == (v[h].upper_bound_holm < 0)


def test_already_suspended_is_never_lifted():
    v = sv.evaluate_hypotheses({"H5": [1.0] * 20}, already_suspended=["H5"])
    assert v["H5"].status == sv.STATUS_SUSPENDED


# --- base : trades, suspension, fail-safe ------------------------------------

def _db(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    with connection_scope(db) as conn:
        conn.execute("INSERT INTO hypothesis_epochs (source, epoch, started_at, description) VALUES "
                     "('hypothesis3_v2', 'P1_29-10', '2026-10-29T19:03:55Z', 'x')")
    return db


def _closed(db, source, asset, r, ouvert_at="2026-10-30T10:00:00+00:00", reel=True, anomalie=None, statut="ferme"):
    with connection_scope(db) as conn:
        tid = conn.execute(
            "INSERT INTO trades (deal_id, source, actif, mode, direction, taille_initiale, prix_entree_prevu, "
            "stop_loss_initial, stop_loss_courant, risque_eur, pourcentage_risque_applique, ouvert_at, statut, "
            "r_multiple_total, anomalie_technique) VALUES ('d', ?, ?, 'demo', 'long', 1, 100, 99, 99, 5, 1, ?, ?, ?, ?)",
            (source, asset, ouvert_at, statut, r, anomalie),
        ).lastrowid
        conn.execute("INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at, "
                     "prix_sortie_reel) VALUES (?, 'sl', 1.0, 99, ?, 'x', '2026-10-30T11:00:00Z', ?)",
                     (tid, r, 99.0 if reel else None))
        conn.execute("INSERT INTO trade_partials (trade_id, palier, fraction, prix_sortie, r_atteint, motif, executed_at) "
                     "VALUES (?, 'tp1', 0.0, 99, 1, 'non rempli', '2026-10-30T11:00:00Z')", (tid,))


def test_fetch_filters_unreconciled_old_anomalous_and_open(tmp_path):
    db = _db(tmp_path)
    _closed(db, "hypothesis3_v2", "GBPUSD", -1.0)
    _closed(db, "hypothesis3_v2", "GBPUSD", -1.0, ouvert_at="2026-10-29T10:00:00+00:00")  # avant l'époque
    _closed(db, "hypothesis3_v2", "GBPUSD", -1.0, reel=False)                              # sans prix broker
    _closed(db, "hypothesis3_v2", "GBPUSD", -1.0, anomalie="x")
    _closed(db, "hypothesis3_v2", "GBPUSD", -1.0, statut="ouvert")
    _closed(db, "station_x", "GOLD", -1.0)
    rows = sv.fetch_post_epoch_trades(db, "2026-10-29T19:03:55Z")
    assert len(rows) == 1 and rows[0]["actif"] == "GBPUSD"


def test_daily_check_suspends_writes_journal_and_is_idempotent(tmp_path):
    db = _db(tmp_path)
    for i in range(15):
        _closed(db, "hypothesis3_v2", "GBPUSD" if i % 2 else "USDJPY", -1.0 - 0.02 * i)
    verdicts, newly, trades = sv.run_daily_check(db, "2026-11-20T06:30:00+00:00")
    assert newly == ["H3"] and len(trades) == 15
    assert sv.is_source_suspended(db, "hypothesis3_v2") is True
    assert sv.is_source_suspended(db, "hypothesis_v2") is False
    assert sv.is_source_suspended(db, "station_x") is False
    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM logs WHERE module = 'surveillance'").fetchone()[0] == 1
    conn.close()
    _, newly2, _ = sv.run_daily_check(db, "2026-11-21T06:30:00+00:00")
    assert newly2 == []  # idempotent : pas de seconde suspension ni de seconde ligne
    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM hypothesis_suspensions").fetchone()[0] == 1
    conn.close()


def test_daily_check_without_epoch_does_nothing(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    verdicts, newly, trades = sv.run_daily_check(db, "2026-11-20T06:30:00+00:00")
    assert newly == [] and trades == [] and all(v.status == sv.STATUS_INCONCLUSIVE for v in verdicts.values())


def test_is_source_suspended_fails_closed_on_read_error(tmp_path, monkeypatch):
    db = _db(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("db illisible")

    monkeypatch.setattr(sv, "connection_scope", boom)
    assert sv.is_source_suspended(db, "hypothesis3_v2") is True      # fail-safe : entrées bloquées
    assert sv.is_source_suspended(db, "station_x") is False          # source hors hypothèses : non concernée


def test_missing_table_is_not_a_suspension(tmp_path):
    db = _db(tmp_path)
    assert sv.is_source_suspended(db, "hypothesis3_v2") is False


# --- état quotidien + script ---------------------------------------------------

def test_state_file_lists_each_hypothesis_and_asset(tmp_path):
    db = _db(tmp_path)
    for i in range(4):
        _closed(db, "hypothesis3_v2", "GBPUSD", 0.5)
    out = tmp_path / "out"
    assert controle.main(db_path=db, out_dir=str(out)) == 0
    text = next(out.glob("etat_*.md")).read_text(encoding="utf-8")
    assert "| H3 | 4 |" in text and "| H3 | GBPUSD | 4 |" in text and sv.STATUS_INCONCLUSIVE in text
    for h in ("H1", "H2", "H4", "H5"):
        assert f"| {h} | 0 |" in text
    assert "| H4 | US30 | 0 |" in text  # actif d'entrée sans trade : ligne n=0


def test_state_marks_assets_of_suspended_hypothesis(tmp_path):
    db = _db(tmp_path)
    for i in range(15):
        _closed(db, "hypothesis3_v2", "GBPUSD", -1.0 - 0.02 * i)
    out = tmp_path / "out"
    controle.main(db_path=db, out_dir=str(out))
    text = next(out.glob("etat_*.md")).read_text(encoding="utf-8")
    assert "| H3 | GBPUSD | 15 |" in text and text.count(sv.STATUS_SUSPENDED) >= 2


def test_regularized_beta_boundaries():
    assert sv._regularized_beta(2.0, 3.0, 0.0) == 0.0
    assert sv._regularized_beta(2.0, 3.0, 1.0) == 1.0


def test_state_marks_active_asset_with_enough_trades(tmp_path):
    db = _db(tmp_path)
    for i in range(16):
        _closed(db, "hypothesis3_v2", "GBPUSD", 0.5 if i % 2 else -0.3)
    out = tmp_path / "out"
    controle.main(db_path=db, out_dir=str(out))
    text = next(out.glob("etat_*.md")).read_text(encoding="utf-8")
    assert "| H3 | GBPUSD | 16 |" in text and f"| {sv.STATUS_ACTIVE} |" in text
