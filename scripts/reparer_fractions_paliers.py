"""Réparation déterministe des trades à paliers dont les fractions de
clôture ne somment pas à 1 (cause : `taille_initiale` jamais ramenée à la
taille réellement ouverte, voir docs/corrections_hypotheses_2026-10-29.md,
Suite). Pour un trade 'ouvert' dont tous les paliers sont terminés
(ferme/annule) : taille_initiale := somme des paliers remplis ; chaque
fraction est remise à l'échelle (fraction × ancienne / nouvelle taille) ;
le risque est réduit d'autant (jamais augmenté). Si la somme obtenue
n'est pas 1 à 1e-6 près, rien n'est modifié et le trade est marqué
`anomalie_technique` (non exploitable). Sans --apply : simulation.
Usage : python scripts/reparer_fractions_paliers.py [db] [--apply]"""
import sqlite3
import sys

TOLERANCE = 1e-6


def repair(db_path: str, apply: bool = False) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    report = []
    try:
        trades = conn.execute(
            "SELECT id, taille_initiale FROM trades WHERE statut = 'ouvert' "
            "AND EXISTS (SELECT 1 FROM trade_legs l WHERE l.trade_id = trades.id) "
            "AND NOT EXISTS (SELECT 1 FROM trade_legs l WHERE l.trade_id = trades.id AND l.statut IN ('ouvert', 'en_attente'))"
        ).fetchall()
        for trade in trades:
            partials = conn.execute("SELECT id, fraction FROM trade_partials WHERE trade_id = ?", (trade["id"],)).fetchall()
            total = sum(p["fraction"] for p in partials)
            if not partials or abs(total - 1.0) <= TOLERANCE:
                continue
            filled = conn.execute(
                "SELECT COALESCE(SUM(taille), 0) AS s FROM trade_legs WHERE trade_id = ? AND statut = 'ferme'", (trade["id"],)
            ).fetchone()["s"]
            old = trade["taille_initiale"]
            repaired_total = total * old / filled if filled > 0 and old else None
            if repaired_total is None or abs(repaired_total - 1.0) > TOLERANCE or filled > old + 1e-12:
                report.append((trade["id"], "non_reparable", total))
                if apply:
                    conn.execute(
                        "UPDATE trades SET anomalie_technique = 'fractions_paliers_incoherentes' WHERE id = ?", (trade["id"],)
                    )
                continue
            report.append((trade["id"], "reparable", total))
            if apply:
                factor = filled / old
                conn.execute(
                    "UPDATE trades SET taille_initiale = ?, risque_eur = risque_eur * ?, "
                    "pourcentage_risque_applique = pourcentage_risque_applique * ? WHERE id = ?",
                    (filled, factor, factor, trade["id"]),
                )
                for p in partials:
                    conn.execute("UPDATE trade_partials SET fraction = ? WHERE id = ?", (p["fraction"] * old / filled, p["id"]))
        if apply:
            conn.commit()
    finally:
        conn.close()
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for row in repair(args[0] if args else "data/assistant_trading.db", apply="--apply" in sys.argv):
        print(row)
