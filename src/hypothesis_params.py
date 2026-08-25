"""
hypothesis_params.py — Application en direct des paramètres validés par
le cycle d'évolution H2-H5 (§2.11/§3.9, voir docs/HYPOTHESES.md 25/08/2026
"cycle 2"). Réutilise la table `rule_changes` déjà présente au schéma
(§3.8) plutôt que d'en créer une nouvelle : `variable` = "H{n}.<nom>",
`ajustement_propose` = nouvelle valeur (texte), `statut='applique'` pour
un override actif.

**Jamais appelé en cours de run** — uniquement au DÉMARRAGE de chaque
`hypothesisN_executor.py`, cohérent avec le principe "code-locked entre
deux redémarrages" (invariant #4) : un override ne prend effet qu'après
un redémarrage explicite du process concerné, jamais en silence pendant
qu'il tourne.

Fail-safe par construction (invariant #7) : toute erreur de lecture
(base absente, ligne malformée, table absente) retourne la valeur codée
en dur du module appelant — jamais une exception qui bloquerait le
démarrage d'un process de trading.
"""

import sqlite3
from typing import Dict, List, Optional

from src.db import get_connection


def get_active_override(db_path: str, hypothesis: str, variable_name: str) -> Optional[str]:
    """Dernière valeur appliquée (statut='applique') pour
    "{hypothesis}.{variable_name}", ou None si absente/erreur. Retourne
    toujours une chaîne brute — la conversion de type est à la charge de
    l'appelant (apply_overrides le fait pour le cas générique)."""
    variable = f"{hypothesis}.{variable_name}"
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT ajustement_propose FROM rule_changes "
                "WHERE variable = ? AND statut = 'applique' "
                "ORDER BY applied_at DESC LIMIT 1",
                (variable,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row["ajustement_propose"] if row is not None else None


def apply_overrides(module, hypothesis: str, db_path: str, param_names: List[str]) -> Dict[str, object]:
    """Pour chaque nom de paramètre déjà présent comme attribut de
    `module`, lit un override actif et le réapplique via setattr — même
    technique que les context managers de recherche
    (`scripts/evaluate_hypothesis_candidates.py::_override_attrs`), mais
    PERMANENTE (pas restaurée à la sortie). Type de la valeur déduit du
    type actuel de l'attribut (int/float/str) — une valeur non
    convertible est ignorée, jamais une exception propagée."""
    applied: Dict[str, object] = {}
    for name in param_names:
        raw = get_active_override(db_path, hypothesis, name)
        if raw is None:
            continue
        if not hasattr(module, name):
            continue
        current = getattr(module, name)
        try:
            typed_value = type(current)(raw)
        except (TypeError, ValueError):
            continue
        setattr(module, name, typed_value)
        applied[name] = typed_value
    return applied


def apply_bollinger_std_override(hypothesis: str, db_path: str, module) -> Optional[float]:
    """Cas particulier de `mean_reversion_strategy.BOLLINGER_STD_MULTIPLIER` :
    paramètre par DÉFAUT de `compute_bollinger_bands`, lu par Python au
    moment de l'appel, pas de la définition — un setattr sur la
    constante du module seule n'aurait aucun effet (déjà rencontré et
    corrigé dans scripts/evaluate_hypothesis_candidates.py, même
    rationale ici, appliqué en permanence plutôt que via un context
    manager)."""
    raw = get_active_override(db_path, hypothesis, "BOLLINGER_STD_MULTIPLIER")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    func = module.compute_bollinger_bands
    old_defaults = func.__defaults__
    if not old_defaults:
        return None
    period = old_defaults[0]
    func.__defaults__ = (period, value)
    module.BOLLINGER_STD_MULTIPLIER = value
    return value


def get_resolution_override(db_path: str, hypothesis: str, axis: str, default: str) -> str:
    """`axis` : "entree" ou "confirmation". Retourne le défaut si aucun
    override actif ou en cas d'erreur (fail-safe, voir docstring de
    module)."""
    raw = get_active_override(db_path, hypothesis, f"resolution_{axis}")
    return raw if raw else default
