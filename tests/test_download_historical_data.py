"""
tests/test_download_historical_data.py — écriture atomique de
`download_one` (29/08/2026, voir docs/DECISIONS.md, incident réel
GOLD_MINUTE_15.json). Ne teste QUE cette fonction (logique de pagination
pure une fois le client simulé) — `main()`/le CLI ne sont jamais testés
ici, script ponctuel, même régime que les autres scripts du projet.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import scripts.download_historical_data as dl
from src.capital_client import CapitalApiError


@pytest.fixture(autouse=True)
def _output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(dl, "THROTTLE_SECONDS", 0.0)
    monkeypatch.setattr("src.retry.time.sleep", lambda *_: None)  # tests rapides, pas d'attente reelle sur retry_with_backoff
    return tmp_path


def _page(n, start=0):
    return [{"snapshotTimeUTC": f"t{start + i}", "closePrice": {"bid": 1.0, "ask": 1.0}} for i in range(n)]


def test_download_one_writes_final_file_only_after_success(_output_dir):
    # error.prices.not-found passe par retry_with_backoff comme toute
    # CapitalApiError (3 tentatives par defaut) avant d'etre relevee a
    # download_one, qui l'interprete alors comme la limite d'historique.
    client = MagicMock()
    client.get.side_effect = [
        {"prices": _page(5, start=10)},
        CapitalApiError("error.prices.not-found"),
        CapitalApiError("error.prices.not-found"),
        CapitalApiError("error.prices.not-found"),
    ]

    points = dl.download_one(client, "GOLD", "HOUR", now=__import__("datetime").datetime(2026, 1, 1))

    final_path = _output_dir / "GOLD_HOUR.json"
    tmp_path_file = _output_dir / "GOLD_HOUR.json.tmp"
    assert len(points) == 5
    assert final_path.exists()
    assert not tmp_path_file.exists()  # renomme, jamais laisse derrière
    assert json.loads(final_path.read_text(encoding="utf-8")) == points


def test_download_one_never_overwrites_existing_file_on_crash(_output_dir):
    # Reproduction de l'incident réel (29/08/2026) : un fichier de
    # production existant et COMPLET ne doit jamais être remplacé par
    # une version partielle si le téléchargement plante en cours de route.
    final_path = _output_dir / "GOLD_HOUR.json"
    original_content = json.dumps([{"snapshotTimeUTC": "original", "closePrice": {"bid": 1.0, "ask": 1.0}}])
    final_path.write_text(original_content, encoding="utf-8")

    client = MagicMock()
    client.get.side_effect = [
        {"prices": _page(5, start=10)},  # une page reussie, ecrite dans le .tmp
        RuntimeError("boom — panne réseau non gérée"),  # plante avant la fin
    ]

    with pytest.raises(RuntimeError):
        dl.download_one(client, "GOLD", "HOUR", now=__import__("datetime").datetime(2026, 1, 1))

    # Le fichier final N'A PAS BOUGÉ malgré la progression partielle.
    assert final_path.read_text(encoding="utf-8") == original_content
    tmp_path_file = _output_dir / "GOLD_HOUR.json.tmp"
    assert tmp_path_file.exists()  # la progression partielle reste dans le .tmp, jamais dans le fichier final
    assert len(json.loads(tmp_path_file.read_text(encoding="utf-8"))) == 5


def test_download_one_empty_first_page_leaves_existing_file_untouched(_output_dir):
    final_path = _output_dir / "GOLD_HOUR.json"
    final_path.write_text("[]", encoding="utf-8")

    client = MagicMock()
    client.get.return_value = {"prices": []}

    points = dl.download_one(client, "GOLD", "HOUR", now=__import__("datetime").datetime(2026, 1, 1))

    assert points == []
    assert final_path.read_text(encoding="utf-8") == "[]"  # jamais touché (aucun point récupéré)


def test_download_one_stops_at_not_found_and_persists_progress_so_far(_output_dir):
    client = MagicMock()
    client.get.side_effect = [
        {"prices": _page(3, start=0)},
        {"prices": _page(2, start=100)},
        CapitalApiError("error.prices.not-found"),
        CapitalApiError("error.prices.not-found"),
        CapitalApiError("error.prices.not-found"),
    ]

    points = dl.download_one(client, "GOLD", "HOUR", now=__import__("datetime").datetime(2026, 1, 1))

    assert len(points) == 5
    final_path = _output_dir / "GOLD_HOUR.json"
    assert json.loads(final_path.read_text(encoding="utf-8")) == points


def test_download_one_reraises_non_not_found_capital_api_error(_output_dir):
    client = MagicMock()
    client.get.side_effect = CapitalApiError("error.too-many.requests")

    with pytest.raises(CapitalApiError):
        dl.download_one(client, "GOLD", "HOUR", now=__import__("datetime").datetime(2026, 1, 1))
