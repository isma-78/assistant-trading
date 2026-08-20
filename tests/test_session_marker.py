"""
Tests de session_marker — marquage de session horaire, collecte
uniquement (§7.2 / demande du 20/08/2026, voir docs/DECISIONS.md).
Fonction pure, 100% couverte.
"""

import pytest

from src.session_marker import compute_market_session


def test_session_asie_start_of_range():
    assert compute_market_session(0) == "asie"


def test_session_asie_before_europe_overlap():
    assert compute_market_session(5) == "asie"


def test_session_asie_europe_overlap_resolves_to_europe():
    # 07h-08h : chevauchement Asie/Europe, priorité Europe (convention documentée).
    assert compute_market_session(7) == "europe"


def test_session_asie_upper_bound_still_covered_by_europe():
    # 8h : hors de la plage Asie (0h-8h exclusif) mais toujours dans
    # Europe (7h-16h) — aucun trou entre les deux sessions.
    assert compute_market_session(8) == "europe"


def test_session_europe_mid_range():
    assert compute_market_session(10) == "europe"


def test_session_europe_us_overlap_resolves_to_us():
    # 13h-16h : chevauchement Europe/US, priorité US (convention documentée).
    assert compute_market_session(13) == "us"
    assert compute_market_session(15) == "us"


def test_session_europe_upper_bound_exclusive_no_overlap():
    assert compute_market_session(16) == "us"


def test_session_us_mid_range():
    assert compute_market_session(18) == "us"


def test_session_us_upper_bound_exclusive():
    assert compute_market_session(21) == "hors_session"


def test_session_hors_session_late_evening():
    assert compute_market_session(23) == "hors_session"


def test_session_invalid_hour_raises():
    with pytest.raises(ValueError):
        compute_market_session(24)
    with pytest.raises(ValueError):
        compute_market_session(-1)
