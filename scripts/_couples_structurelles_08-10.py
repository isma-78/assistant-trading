"""
_couples_structurelles_08-10.py — Étape 1 de la Partie 3 (08/10/2026),
docs/PROTOCOLE_COUPLES_08-10.md §1/§2. Mesure des 3 caractéristiques
structurelles (prix seuls) pour tous les actifs de la configuration
réellement déployée, CHFJPY exclue (protocole).

Lecture seule de `data/historical/`. Aucun calcul de stratégie ici
(réservé à l'étape 2, script séparé).

Usage : python scripts/_couples_structurelles_08-10.py
Sortie : data/snapshots/couples_structurelles_08-10.json + impression.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest_engine import bar_from_raw  # noqa: E402
from src.structural_characteristics import (  # noqa: E402
    cost_over_atr,
    log_returns,
    variance_ratio,
    volatility_clustering,
)

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "historical"
OUT = ROOT / "data" / "snapshots"

ASSETS = ["GOLD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]  # CHFJPY exclue (protocole)
SEALED_START, SEALED_END = "2023-01-01", "2024-06-15"
MIN_DATE = "2019-01-01"

# Fenêtres du protocole §2.
DESCRIPTIVE_WINDOW = (MIN_DATE, "2023-01-01")
FOLD_WINDOWS = {
    "sens1_caracteristique": (MIN_DATE, "2020-01-01"),
    "sens1_esperance": ("2021-01-01", "2023-01-01"),
    "sens2_caracteristique": (MIN_DATE, "2021-01-01"),
    "sens2_esperance": ("2022-01-01", "2023-01-01"),
}


def load_bars(asset: str, resolution: str, start: str, end: str) -> list:
    raw = json.loads((HIST / f"{asset}_{resolution}.json").read_text(encoding="utf-8"))
    bars = [b for b in (bar_from_raw(p) for p in raw) if b is not None and start <= b.time_utc < end]
    assert not any(SEALED_START <= b.time_utc < SEALED_END for b in bars), "fenêtre scellée chargée"
    assert all(b.time_utc >= MIN_DATE for b in bars), "bougie antérieure au 2019-01-01 chargée"
    return bars


def compute_characteristics(bars: list) -> dict:
    returns = log_returns(bars)
    return {
        "n_bars": len(bars),
        "cout_atr": cost_over_atr(bars),
        "persistance_vr8": variance_ratio(returns),
        "regroupement_volatilite": volatility_clustering(returns),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {"fenetre_descriptive": DESCRIPTIVE_WINDOW, "fenetres_plis": FOLD_WINDOWS, "actifs": {}}

    print(f"=== Tableau descriptif (fenêtre {DESCRIPTIVE_WINDOW[0]} -> {DESCRIPTIVE_WINDOW[1]}) ===")
    for asset in ASSETS:
        bars = load_bars(asset, "HOUR", *DESCRIPTIVE_WINDOW)
        chars = compute_characteristics(bars)
        results["actifs"][asset] = {"descriptif": chars}
        print(f"  {asset}: n={chars['n_bars']} cout_atr={chars['cout_atr']} "
              f"persistance_vr8={chars['persistance_vr8']} regroupement_vol={chars['regroupement_volatilite']}")

    print("\n=== Caractéristiques par sens (walk-forward, étape 3) ===")
    for asset in ASSETS:
        for label in ("sens1_caracteristique", "sens2_caracteristique"):
            start, end = FOLD_WINDOWS[label]
            bars = load_bars(asset, "HOUR", start, end)
            chars = compute_characteristics(bars)
            results["actifs"][asset][label] = chars
            print(f"  {asset} [{label}]: n={chars['n_bars']} cout_atr={chars['cout_atr']} "
                  f"persistance_vr8={chars['persistance_vr8']} regroupement_vol={chars['regroupement_volatilite']}")

    (OUT / "couples_structurelles_08-10.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
