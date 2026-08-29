"""
fill_rate_analysis.py — Mesure A du point 10 (29/08/2026, voir
docs/DECISIONS.md et docs/HYPOTHESES.md, amendement du 28/08/2026).

Catégorisation OBLIGATOIRE des non-remplissages (déjà câblée dans
`executor.py` via `annulation_motif`) :
  (a) échec de placement (`rate_limit_429`|`stop_refuse`|
      `autre_echec_placement`) — catégorie OPÉRATIONNELLE, jamais un
      signal de fidélité de marché, EXCLUE du dénominateur ;
  (b) péremption réelle (`peremption_marche`) — seul cas comparable à
      l'absence de modélisation du backtest ;
  (c) rempli (`statut IN ('ouvert', 'ferme')`).

Taux de remplissage = c / (b + c), JAMAIS c / (a+b+c) (mélanger (a)
sous-estimerait le taux pour une raison sans rapport avec le marché —
voir docs/HYPOTHESES.md).

Seuil de décision (déjà fixé, pas recalculé ici) : IC de Wilson à 95%
sur ce taux, borne HAUTE < 0,80 → simulateur déclaré infidèle sur la
jambe remplissage. n minimum = 30 signaux au stade (b+c). Ce module ne
rend AUCUN verdict lui-même (invariant : jamais un jugement caché) — il
calcule les chiffres, le verdict reste écrit à la main dans
docs/DECISIONS.md au moment où n>=30 est atteint.

Couche pure (`wilson_upper_bound`) : 100% couverte. Orchestration
(`aggregate_fill_rate_by_hypothesis_asset`) : lecture DB seule.
"""

import math
from typing import Dict, List, Tuple

from src.db import connection_scope

WILSON_Z_95 = 1.959963984540054  # z bilatéral à 95%, valeur exacte (statistics.NormalDist().inv_cdf(0.975))
FILL_RATE_MIN_N = 30
FILL_RATE_WILSON_UPPER_THRESHOLD = 0.80

_PLACEMENT_FAILURE_MOTIFS = ("rate_limit_429", "stop_refuse", "autre_echec_placement")


def wilson_upper_bound(successes: int, n: int, z: float = WILSON_Z_95) -> float:
    """Borne haute de l'intervalle de Wilson pour une proportion
    binomiale — formule standard, jamais l'approximation normale
    (invalide près de 0 ou 1, exactement le régime où ce seuil de 0,80
    est susceptible d'être testé). `n=0` lève ValueError (jamais un
    taux inventé faute de donnée)."""
    if n <= 0:
        raise ValueError("n doit être > 0 pour calculer un intervalle de Wilson")
    if not 0 <= successes <= n:
        raise ValueError(f"successes ({successes}) doit être entre 0 et n ({n})")
    p_hat = successes / n
    z2 = z * z
    center = p_hat + z2 / (2 * n)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n))
    denom = 1 + z2 / n
    return (center + margin) / denom


def aggregate_fill_rate_by_hypothesis_asset(db_path: str) -> List[Dict[str, object]]:
    """Une ligne par (source, actif) avec au moins un trade — compte
    a/b/c, taux c/(b+c) (`None` si b+c=0), borne haute de Wilson (`None`
    sous ce même b+c=0), et un `verdict` texte parmi
    `"n_insuffisant"`/`"fidele"`/`"infidele"` (jamais calculé ni annoncé
    en dehors de ces chiffres explicites — pas un jugement cascadé)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT source, actif, statut, annulation_motif FROM trades"
        ).fetchall()

    groups: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in rows:
        key = (row["source"], row["actif"])
        counts = groups.setdefault(key, {"a": 0, "b": 0, "c": 0})
        if row["statut"] in ("ouvert", "ferme"):
            counts["c"] += 1
        elif row["statut"] == "annule" and row["annulation_motif"] == "peremption_marche":
            counts["b"] += 1
        elif row["statut"] == "annule" and row["annulation_motif"] in _PLACEMENT_FAILURE_MOTIFS:
            counts["a"] += 1
        # statut='annule' sans annulation_motif connu (trades antérieurs
        # au point 10, jamais rétro-catégorisés — l'information n'existe
        # nulle part, voir docs/DECISIONS.md) : ni compté, ni deviné.

    results = []
    for (source, actif), counts in sorted(groups.items()):
        b_plus_c = counts["b"] + counts["c"]
        if b_plus_c == 0:
            fill_rate, wilson_upper, verdict = None, None, "n_insuffisant"
        else:
            fill_rate = counts["c"] / b_plus_c
            wilson_upper = wilson_upper_bound(counts["c"], b_plus_c)
            if b_plus_c < FILL_RATE_MIN_N:
                verdict = "n_insuffisant"
            else:
                verdict = "infidele" if wilson_upper < FILL_RATE_WILSON_UPPER_THRESHOLD else "fidele"
        results.append({
            "source": source, "actif": actif,
            "echec_placement": counts["a"], "peremption_marche": counts["b"], "rempli": counts["c"],
            "n_b_plus_c": b_plus_c, "taux_remplissage": fill_rate, "wilson_borne_haute": wilson_upper,
            "verdict": verdict,
        })
    return results
