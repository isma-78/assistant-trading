"""
asset_cell_labels.py — Étiquettes des cellules (hypothèse × actif) issues du
protocole pré-enregistré du 05/10/2026 (`docs/PROTOCOLE_EVOLUTION_05-10.md`,
commit 415dbb5), matrice principale 2019-2022 avec modèle de refus de
resserrement (`scripts/_evolution_cells_05-10.py`).

**Étiquetage seulement.** La procédure n'a été validée par le walk-forward
pour AUCUNE hypothèse (docs/APPLICATION_05-10.md). Aucune restriction
d'actifs n'est donc appliquée : toutes les cellules continuent à trader en
démo, époques inchangées. Ces étiquettes servent uniquement à lire les
trades a posteriori (par exemple : les trades H5 sur les cellules
« retenue » se comportent-ils autrement ?). Elles ne sont lues par aucun
exécuteur et ne peuvent rien bloquer.
"""

RETAINED = "retenue"
NOT_RETAINED = "non retenue"
NOT_CLASSIFIABLE = "non classable"

_ALL_NOT_RETAINED = {a: NOT_RETAINED for a in ("GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD")}

CELL_LABELS = {
    "hypothesis_v2": {**_ALL_NOT_RETAINED, "CHFJPY": NOT_RETAINED},
    "hypothesis2_v2": {**_ALL_NOT_RETAINED, "CHFJPY": NOT_CLASSIFIABLE},
    "hypothesis3_v2": {**_ALL_NOT_RETAINED, "CHFJPY": NOT_CLASSIFIABLE},
    "hypothesis4_v2": {**_ALL_NOT_RETAINED, "CHFJPY": NOT_CLASSIFIABLE},
    "hypothesis5_v2": {
        **_ALL_NOT_RETAINED, "US100": RETAINED, "US30": RETAINED, "EURUSD": RETAINED, "CHFJPY": NOT_CLASSIFIABLE,
    },
}

PROCEDURE_VALIDATED = {source: False for source in CELL_LABELS}


def label_for(source: str, asset: str) -> str:
    """Étiquette d'une cellule, « non classable » si inconnue."""
    return CELL_LABELS.get(source, {}).get(asset, NOT_CLASSIFIABLE)


def restricted_assets(source: str) -> tuple:
    """Actifs à RETIRER du trading pour `source`. Toujours vide tant que la
    procédure n'est pas validée pour cette hypothèse (protocole §5)."""
    if not PROCEDURE_VALIDATED.get(source, False):
        return ()
    return tuple(a for a, label in CELL_LABELS[source].items() if label == NOT_RETAINED)
