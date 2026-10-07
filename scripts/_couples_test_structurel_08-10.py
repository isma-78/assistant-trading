"""
_couples_test_structurel_08-10.py — Étape 3 de la Partie 3 (08/10/2026),
docs/PROTOCOLE_COUPLES_08-10.md §4/§5. Consomme les sorties des étapes 1
et 2 (déjà écrites en JSON), calcule la corrélation de Spearman + test
de permutation par hypothèse et par sens, applique la règle de
validation à 2 sens (Bonferroni m=5).

Lecture seule de data/snapshots/couples_structurelles_08-10.json et
couples_decouverte_08-10.json (déjà produits). Aucun calcul de
stratégie ici.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.structural_test import permutation_p_value  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "snapshots"

BONFERRONI_M = 5
MIN_ASSETS_ELIGIBLE = 6
ALPHA = 0.05

# Caractéristique unique par hypothèse + sens attendu (docs/PROTOCOLE_COUPLES_08-10.md §0bis).
HYP_CHARACTERISTIC = {
    "H1": ("persistance_vr8", "positif"),
    "H2": ("persistance_vr8", "positif"),
    "H3": ("cout_atr", "negatif"),
    "H4": ("persistance_vr8", "negatif"),
    "H5": ("regroupement_volatilite", "positif"),
}
SENS_TO_CHAR_WINDOW = {"sens1": "sens1_caracteristique", "sens2": "sens2_caracteristique"}


def evaluate_sens(hyp_key, sens_label, structurelles, decouverte):
    char_key, expected_sign = HYP_CHARACTERISTIC[hyp_key]
    char_window = SENS_TO_CHAR_WINDOW[sens_label]
    pairs = []
    for asset, cell in decouverte[sens_label][hyp_key].items():
        if not cell["eligible"]:
            continue
        char_value = structurelles["actifs"][asset][char_window][char_key]
        if char_value is None:
            continue
        pairs.append((asset, char_value, cell["esperance_contractee"]))

    n_eligible = len(pairs)
    if n_eligible < MIN_ASSETS_ELIGIBLE:
        return {"n_eligible": n_eligible, "statut": "indémontrable", "rho": None, "p_corrige": None, "assets": [a for a, _, _ in pairs]}

    x = [c for _, c, _ in pairs]
    y = [e for _, _, e in pairs]
    rho, p = permutation_p_value(x, y)
    p_corrige = min(1.0, p * BONFERRONI_M) if p is not None else None
    sign_ok = rho is not None and ((rho > 0) == (expected_sign == "positif"))
    significatif = p_corrige is not None and p_corrige < ALPHA

    if sign_ok and significatif:
        statut = "validé"
    elif (not sign_ok) and significatif:
        statut = "rejet_net"  # signe contraire, significatif
    else:
        statut = "indémontrable"

    return {
        "n_eligible": n_eligible, "rho": rho, "p_corrige": p_corrige,
        "sign_ok": sign_ok, "statut": statut, "assets": [a for a, _, _ in pairs],
    }


def combine(sens1_result, sens2_result):
    if sens1_result["statut"] == "validé" and sens2_result["statut"] == "validé":
        return "structurellement validée"
    if sens1_result["statut"] == "rejet_net" or sens2_result["statut"] == "rejet_net":
        return "non validée"
    return "indémontrable"


def main() -> int:
    structurelles = json.loads((OUT / "couples_structurelles_08-10.json").read_text(encoding="utf-8"))
    decouverte = json.loads((OUT / "couples_decouverte_08-10.json").read_text(encoding="utf-8"))

    results = {}
    for hyp_key, (char_key, expected_sign) in HYP_CHARACTERISTIC.items():
        print(f"\n===== {hyp_key} (caractéristique={char_key}, sens attendu={expected_sign}) =====")
        sens1 = evaluate_sens(hyp_key, "sens1", structurelles, decouverte)
        sens2 = evaluate_sens(hyp_key, "sens2", structurelles, decouverte)
        final_status = combine(sens1, sens2)
        print(f"  sens1 (carac 2019-2020 -> espérance 2021-2023) : {sens1}")
        print(f"  sens2 (carac 2019-2021 -> espérance 2022-2023) : {sens2}")
        print(f"  STATUT FINAL : {final_status}")
        results[hyp_key] = {"caracteristique": char_key, "sens_attendu": expected_sign,
                             "sens1": sens1, "sens2": sens2, "statut_final": final_status}

    (OUT / "couples_test_structurel_08-10.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
