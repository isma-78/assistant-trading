"""
epoch_status.py — Compteur de confirmation par hypothèse (protocole E1,
25/09/2026, voir docs/DECISIONS.md). Lecture seule.

Pour chaque source, ne compte QUE les trades ouverts depuis le début de
l'époque en vigueur (table `hypothesis_epochs`, dernière ligne par source)
et sans `anomalie_technique` — jamais un trade d'une époque antérieure.
Seuil de verdict : max(30, 10 × nombre de variables ajustées), rappelé
ici, jamais appliqué automatiquement : ce script ne rend aucun verdict.

Usage : python scripts/epoch_status.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.verdict_counter import count_all  # noqa: E402

# Variables ajustées par hypothèse (pré-inscription du 29/08/2026 + toute
# idée déployée depuis, voir docs/DECISIONS.md) — à mettre à jour à chaque
# nouvelle variable déployée, jamais déduit des données.
ADJUSTED_VARIABLES = {
    "hypothesis_v2": 3,
    "hypothesis2_v2": 4,
    "hypothesis3_v2": 3,
    "hypothesis4_v2": 3,
    "hypothesis5_v2": 4,  # + TSMOM_LOOKBACK_DAYS (E2-H5, 25/09/2026)
}


def verdict_threshold(source: str) -> int:
    return max(30, 10 * ADJUSTED_VARIABLES.get(source, 5))


def main() -> None:
    # A9 (bilan du 05/10/2026) : comptage délégué à src/verdict_counter.py
    # (CHFJPY exclue de H2-H5, « réconcilié » = prix broker sur chaque
    # sortie), partagé avec les alertes de jalons — jamais deux définitions.
    for count in count_all(load_config().db_path):
        mean = f"{count.mean_r:+.3f}" if count.mean_r is not None else "  -   "
        print(
            f"{count.source:15s} époque={count.epoch:30s} depuis {count.started_at}  "
            f"réconciliés={count.n:3d}/{verdict_threshold(count.source)}  E[R]={mean}  "
            f"sans_prix_broker={count.closed_without_broker_price}  ouverts={count.open_trades}  "
            f"fantômes={count.ghosts}  CHFJPY_exclus={count.chfjpy_excluded}"
        )


if __name__ == "__main__":
    main()
