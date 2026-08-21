"""Garde-fou de cohérence entre les 4 copies de `_normalize_source` /
`_envelope_source_key` (executor.py, metrics.py, circuit_breaker_store.py,
confidence_scorer.py) — dupliquées par convention du projet (nom privé,
pas d'import inter-module) plutôt que centralisées. Ce doublon est un
risque réel de divergence (trouvé le 21/08/2026 : les 4 copies ne
reconnaissaient QUE "hypothesis", voir docs/DECISIONS.md) — ce test ne
supprime pas le risque mais le rend impossible à introduire en silence :
toute future hypothèse ajoutée à une seule des 4 copies (oubli) fait
échouer ce test plutôt que de mélanger silencieusement des statistiques.
"""

from src.circuit_breaker_store import _normalize_source as cb_normalize
from src.confidence_scorer import _normalize_source as cs_normalize
from src.executor import _envelope_source_key as executor_normalize
from src.metrics import _normalize_source as metrics_normalize

_ALL_NORMALIZERS = [executor_normalize, cb_normalize, metrics_normalize, cs_normalize]

_TEST_SOURCES = [
    "hypothesis", "hypothesis3", "hypothesis2",
    "-1002481537588",  # id de canal brut Station X (voir CLAUDE.md)
    "stationx", "",  "hypothesis4",  # source inconnue future : doit retomber sur stationx partout
]


def test_all_four_normalizers_agree_on_every_source():
    for source in _TEST_SOURCES:
        results = {fn.__module__: fn(source) for fn in _ALL_NORMALIZERS}
        assert len(set(results.values())) == 1, f"Désaccord sur {source!r} : {results}"


def test_known_hypothesis_sources_map_to_themselves():
    for source in ("hypothesis", "hypothesis3", "hypothesis2"):
        for fn in _ALL_NORMALIZERS:
            assert fn(source) == source


def test_unknown_sources_collapse_to_stationx():
    for source in ("-1002481537588", "stationx", "", "hypothesis4"):
        for fn in _ALL_NORMALIZERS:
            assert fn(source) == "stationx"
