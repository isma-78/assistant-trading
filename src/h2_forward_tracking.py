"""
h2_forward_tracking.py — Point 3 (30/08/2026, voir docs/DECISIONS.md) :
suivi FORWARD de H2/L2, le seul chiffre qui compte désormais — le
backtest confirmé (+0,2431R) s'est révélé reposer sur un bug de
lookahead réel (point 1, même jour) et est déclaré invalide. Un edge
forward mesuré sur donnée live, où aucun lookahead n'est
structurellement possible (le futur n'existe pas encore), serait la
seule preuve directe qu'il existe quelque chose de réel derrière ce
chiffre.

**Jalons pré-enregistrés, vérifiés indépendamment le 30/08/2026 avant
tout regard sur la donnée forward** (σ=1,075307R et mean=0,2431R
mesurés sur les 389 trades de confirmation, voir docs/DECISIONS.md,
point 4a) :
- n=53 : seuil auquel la borne basse à 95% (m=1, un seul suivi
  pré-engagé) dépasserait zéro SI l'effet vrai était encore +0,2431R —
  `(z95 × σ / mean)² ≈ 52,94`.
- n=121 : seuil de détection à 80% de puissance dans les mêmes
  conditions — `((z95 + z_β80) × σ / mean)² ≈ 120,97`.
- Cadence de référence (389 trades / 17,5 mois de confirmation)
  ≈ 22 trades/mois → n=53 en ~2,4 mois, n=121 en ~5,4 mois.

**Alerte pré-enregistrée AVANT tout regard sur la donnée forward** :
si à n≥30 l'espérance forward observée est déjà négative, c'est un
signal de DÉFAUT (pas de variance) à signaler immédiatement — jamais
attendre n=53 dans ce cas précis.

CE MODULE NE FAIT PAS : il ne conclut RIEN sous n=53 (aucun verdict de
fidélité de signal, jamais une lecture optimiste ou pessimiste
anticipée). Il ne recalcule jamais `H2_CONFIRMED_R` — c'est une
référence de comparaison FIXE, écrite avant tout regard sur cette
donnée, jamais mise à jour a posteriori.

`H2_FORWARD_CUTOFF` : horodatage exact du redémarrage du 30/08/2026
avec le correctif du point 1 déployé — tout trade H2 antérieur reposait
sur l'ancien comportement LIVE (bougie HOUR_4/DAY encore en formation
incluse dans la confluence) et n'est PAS un trade forward valide pour
ce suivi (voir docs/DECISIONS.md, entrée "correctif lookahead
déployé").

Deux couches, même convention que le reste du projet :
- Calcul pur : aucun ici au-delà de la réutilisation directe de
  `evolution_engine.compute_lower_bound` (déjà 100% couvert ailleurs).
- Orchestration I/O (`h2_forward_trades`, `summarize_h2_forward`) :
  lecture DB seule, aucune écriture.
"""

from typing import Dict, List, Optional

from src.db import connection_scope
from src.evolution_engine import compute_lower_bound

H2_FORWARD_CUTOFF = "2026-08-30T07:02:15+00:00"

# Références FIXES, écrites AVANT tout regard sur la donnée forward —
# jamais recalculées à partir des trades forward eux-mêmes.
H2_CONFIRMED_R = 0.2431
H2_CONFIRMED_SIGMA = 1.075307

MILESTONE_LOWER_BOUND_POSITIVE_N = 53
MILESTONE_80_PERCENT_POWER_N = 121
MIN_N_FOR_NEGATIVE_ALERT = 30
Z_95_ONE_SIDED = 1.6449  # m=1 : un seul suivi forward pré-engagé sur H2, aucune comparaison multiple ici

# Jalons par TAILLE D'EFFET VRAIE HYPOTHÉTIQUE (02/09/2026, voir
# docs/Prompt_Apres_Lookahead_31-08.md point 4c et docs/DECISIONS.md,
# "suite du chantier post-lookahead") — les jalons ci-dessus (n=53/121)
# supposent que l'effet vrai reste +0,2431R, or ce chiffre vient du
# backtest confirmé DÉSORMAIS INVALIDE (bug de lookahead, point 1). Ces
# jalons-ci ne font PAS cette hypothèse : ils répondent à "si l'effet vrai
# est E, à quel n la borne basse à 95% (m=1) dépasse zéro ?" pour
# plusieurs valeurs plausibles de E, du même calcul que ci-dessus
# ((z95 × σ / E)², σ=H2_CONFIRMED_SIGMA=1,075307R, seule constante
# réutilisée du backtest invalide — un écart-type n'est pas un signe
# d'edge, sa contamination par le lookahead est nettement moins probable
# qu'une moyenne gonflée par un biais optimiste unidirectionnel).
# Arrondi au plus proche (pas un plafond) — coïncide avec un plafond
# pour n=53/121 ci-dessus mais pas pour ces 4 valeurs, voir
# tests/test_h2_forward_tracking.py::test_effect_size_milestones_match_lower_bound_formula.
# Cadence de référence : ~25 trades/mois sur les 9 actifs H2
# (HYPOTHESIS2_ASSETS), légèrement au-dessus du 22/mois historique
# (389 trades/17,5 mois) depuis l'ajout de CHFJPY.
EFFECT_SIZE_MILESTONES: Dict[float, int] = {
    0.20: 78,    # ≈ 3 mois
    0.15: 139,   # ≈ 5,6 mois
    0.10: 313,   # ≈ 12,5 mois
    0.05: 1251,  # ≈ 50 mois — hors de portée en délai utile
}


def h2_forward_trades(db_path: str, cutoff: str = H2_FORWARD_CUTOFF) -> List[Dict[str, object]]:
    """Trades `hypothesis2_v2` réellement fermés depuis `cutoff`
    (défaut : le redémarrage corrigé du 30/08/2026) — jamais un trade
    antérieur, qui reposerait sur l'ancien comportement live (voir
    docstring du module)."""
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT id, actif, r_multiple_total, ouvert_at, ferme_at FROM trades "
            "WHERE source = 'hypothesis2_v2' AND statut = 'ferme' AND ouvert_at >= ? "
            "AND r_multiple_total IS NOT NULL ORDER BY ouvert_at",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def summarize_h2_forward(db_path: str, cutoff: str = H2_FORWARD_CUTOFF) -> Dict[str, object]:
    """Chiffres forward de H2 : n, moyenne, borne basse à 95% (z fixé
    au pré-enregistrement de ce suivi, jamais recalculé), écart à
    `H2_CONFIRMED_R` (référence de comparaison, jamais une cible
    garantie). `alerte` non-None dès que la règle du point 3 est
    déclenchée (n>=30 ET moyenne < 0). `verdict` reste
    `"aucun_verdict_avant_n53"` tant que ce seuil n'est pas atteint —
    jamais une lecture anticipée."""
    trades = h2_forward_trades(db_path, cutoff)
    n = len(trades)
    r_values = [t["r_multiple_total"] for t in trades]
    mean_r = sum(r_values) / n if n else None
    lower_bound = compute_lower_bound(r_values, Z_95_ONE_SIDED) if n >= 2 else None

    alert: Optional[str] = None
    if n >= MIN_N_FOR_NEGATIVE_ALERT and mean_r is not None and mean_r < 0:
        alert = (
            f"ALERTE : espérance forward négative ({mean_r:.4f}R) à n={n} >= {MIN_N_FOR_NEGATIVE_ALERT} — "
            f"écart de cette ampleur avec le backtest (+{H2_CONFIRMED_R}R) signale un défaut, pas de la variance."
        )

    verdict = "aucun_verdict_avant_n53"
    if n >= MILESTONE_LOWER_BOUND_POSITIVE_N:
        verdict = "borne_basse_positive" if (lower_bound is not None and lower_bound > 0) else "borne_basse_non_positive"

    return {
        "n": n,
        "mean_r": mean_r,
        "lower_bound_95": lower_bound,
        "ecart_vs_confirme": (mean_r - H2_CONFIRMED_R) if mean_r is not None else None,
        "alerte": alert,
        "verdict": verdict,
    }
