"""
structural_test.py — Partie 3 (08/10/2026), docs/PROTOCOLE_COUPLES_08-10.md
§4/§5. Corrélation de Spearman entre une caractéristique structurelle et
l'espérance nette contractée par actif, avec test de permutation. Calcul
pur, aucune I/O.
"""

import random
from typing import Optional, Sequence, Tuple


def rank(values: Sequence[float]) -> list:
    """Rangs avec moyenne des rangs pour les ex-aequo (convention
    standard de Spearman) — 1 = la plus petite valeur."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # +1 : rangs 1-indexés
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n == 0:
        return None
    mean_x, mean_y = sum(x) / n, sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Corrélation de Spearman = Pearson sur les rangs. `None` si moins
    de 2 points, ou si l'une des deux séries est constante (variance de
    rangs nulle — n'arrive qu'avec un seul point ou des ex-aequo
    totaux)."""
    if len(x) != len(y) or len(x) < 2:
        return None
    return _pearson(rank(x), rank(y))


def permutation_p_value(
    x: Sequence[float], y: Sequence[float], reps: int = 10_000, seed: int = 20261008,
) -> Tuple[Optional[float], Optional[float]]:
    """(rho observé, p bilatéral) : p = part des `reps` permutations
    aléatoires de `y` dont `|rho_permuté| >= |rho_observé|`. `(None,
    None)` si `spearman_rho` ne peut être calculée sur les données
    observées elles-mêmes."""
    observed = spearman_rho(x, y)
    if observed is None:
        return None, None
    rng = random.Random(seed)
    y_list = list(y)
    count = 0
    for _ in range(reps):
        shuffled = y_list[:]
        rng.shuffle(shuffled)
        permuted_rho = spearman_rho(x, shuffled)
        if permuted_rho is not None and abs(permuted_rho) >= abs(observed):
            count += 1
    return observed, count / reps
