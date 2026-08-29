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
    "hypothesis", "hypothesis3", "hypothesis2", "hypothesis4", "hypothesis5",
    "hypothesis_backtest", "hypothesis2_backtest", "hypothesis3_backtest",
    "hypothesis4_backtest", "hypothesis5_backtest",  # backtest rétrospectif, 24/08/2026
    "hypothesis_v2", "hypothesis2_v2", "hypothesis3_v2", "hypothesis4_v2", "hypothesis5_v2",
    "hypothesis_v2_backtest", "hypothesis2_v2_backtest", "hypothesis3_v2_backtest",
    "hypothesis4_v2_backtest", "hypothesis5_v2_backtest",  # refonte L1-L5, 29/08/2026
    "-1002481537588",  # id de canal brut Station X (voir CLAUDE.md)
    "stationx", "", "hypothesis6",  # source inconnue future : doit retomber sur stationx partout
]


def test_all_four_normalizers_agree_on_every_source():
    for source in _TEST_SOURCES:
        results = {fn.__module__: fn(source) for fn in _ALL_NORMALIZERS}
        assert len(set(results.values())) == 1, f"Désaccord sur {source!r} : {results}"


def test_known_hypothesis_sources_map_to_themselves():
    # hypothesis4 (Hypothèse #4, 21/08/2026) et hypothesis5 (Hypothèse #5,
    # 23/08/2026, voir docs/HYPOTHESES.md) sont désormais connues des 4
    # copies — comme H1/H2/H3, malgré une exécution non encore câblée
    # (aucun identifiant .env, aucun déploiement).
    for source in ("hypothesis", "hypothesis3", "hypothesis2", "hypothesis4", "hypothesis5"):
        for fn in _ALL_NORMALIZERS:
            assert fn(source) == source


def test_known_backtest_sources_map_to_themselves():
    # Backtest rétrospectif (24/08/2026, voir docs/HYPOTHESES.md) —
    # sources dédiées, jamais mélangées aux sources live ci-dessus.
    for source in (
        "hypothesis_backtest", "hypothesis2_backtest", "hypothesis3_backtest",
        "hypothesis4_backtest", "hypothesis5_backtest",
    ):
        for fn in _ALL_NORMALIZERS:
            assert fn(source) == source


def test_known_v2_sources_map_to_themselves_and_never_to_v1():
    # Refonte L1-L5 (29/08/2026, voir docs/DECISIONS.md/HYPOTHESES.md) :
    # les nouvelles sources `_v2` ne doivent JAMAIS s'agréger avec leurs
    # équivalents v1 (ancien code, archivé) — un garde-fou de cohérence
    # supplémentaire ne suffit pas à empêcher une requête ailleurs dans
    # le projet de faire un LIKE/préfixe (vérifié séparément, voir
    # docs/DECISIONS.md), mais celui-ci vérifie au moins qu'aucune des 4
    # copies de normalisation elle-même ne confond les deux versions.
    v1_to_v2 = {
        "hypothesis": "hypothesis_v2", "hypothesis2": "hypothesis2_v2", "hypothesis3": "hypothesis3_v2",
        "hypothesis4": "hypothesis4_v2", "hypothesis5": "hypothesis5_v2",
        "hypothesis_backtest": "hypothesis_v2_backtest", "hypothesis2_backtest": "hypothesis2_v2_backtest",
        "hypothesis3_backtest": "hypothesis3_v2_backtest", "hypothesis4_backtest": "hypothesis4_v2_backtest",
        "hypothesis5_backtest": "hypothesis5_v2_backtest",
    }
    for v1_source, v2_source in v1_to_v2.items():
        for fn in _ALL_NORMALIZERS:
            assert fn(v2_source) == v2_source
            assert fn(v2_source) != fn(v1_source)


def test_unknown_sources_collapse_to_stationx():
    # "hypothesis6" représente une hypothèse future PAS ENCORE ajoutée à
    # _KNOWN_HYPOTHESIS_SOURCES — doit rester repliée sur "stationx"
    # partout tant qu'elle n'a pas reçu le même traitement que H4/H5 ici.
    for source in ("-1002481537588", "stationx", "", "hypothesis6"):
        for fn in _ALL_NORMALIZERS:
            assert fn(source) == "stationx"
