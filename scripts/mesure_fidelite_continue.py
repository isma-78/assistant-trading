"""
mesure_fidelite_continue.py — Remesure mensuelle de la fidélité H1-H4
(07/10/2026, étape 4 du mandat), selon
`docs/PROTOCOLE_FIDELITE_07-10.md`. Lecture seule de la base de
production ET de `data/historical/` (suppose que l'historique est
rafraîchi par ailleurs — `scripts/download_historical_data.py`, jamais
par ce script). AUCUN appel broker ici, AUCUNE action sur un exécuteur
ou une configuration. Alerte Telegram uniquement au premier
franchissement de n=20 paires pour une hypothèse (jamais à chaque
mesure).

**NE PAS lancer avant que `data/historical/` couvre une période
postérieure au 2026-09-25T18:00:00** (sinon la mesure reste bornée à la
même fenêtre pré-E1 que le 07/10/2026, sans valeur ajoutée — voir
docs/FIDELITE_07-10.md).

Cron prévu (1er de chaque mois, à installer après le 10/10) :
    0 6 1 * * cd /home/assistant/assistant-trading && venv/bin/python scripts/mesure_fidelite_continue.py >> logs/mesure_fidelite_continue_cron.log 2>&1
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.evolution_v2_test import TradeRef  # noqa: E402
from src.fidelity_continuous import compute_update, format_first_n20_message, format_monthly_summary_line  # noqa: E402
from src.fidelity_measurement import compute_fidelity_metrics  # noqa: E402

HYPOTHESES = ["H1", "H2", "H3", "H4"]
SOURCE_BY_HYP = {"H1": "hypothesis_v2", "H2": "hypothesis2_v2", "H3": "hypothesis3_v2", "H4": "hypothesis4_v2"}
STATE_KEY = "fidelity_continuous:{hyp}:n_paired"
STATUS_KEY = "fidelity_continuous:{hyp}:status"


def load_live(conn, source, cutoff):
    rows = conn.execute(
        "SELECT actif, direction, ouvert_at, r_multiple_total FROM trades WHERE source = ? AND statut = 'ferme' "
        "AND r_multiple_total IS NOT NULL AND ouvert_at < ? ORDER BY ouvert_at", (source, cutoff),
    ).fetchall()
    return [TradeRef(r["actif"], r["direction"], r["ouvert_at"], r["r_multiple_total"]) for r in rows]


def load_backtest_replayer():
    """Import différé : ce script réutilise la même logique de rejeu que
    `scripts/_fidelite_h1h4_07-10.py` (jamais dupliquée) — importé comme
    module ponctuel plutôt que recopié. Le chemin du fichier (tiret dans
    le nom) exige un chargement par `importlib`, comme les autres
    scripts `_*` de ce projet."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fidelite_h1h4_07-10.py")
    spec = importlib.util.spec_from_file_location("_fidelite_h1h4_07_10", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    from src.audit_notifier import send_notification
    from src.config import load_config

    config = load_config()
    replayer = load_backtest_replayer()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    summary_lines = []
    try:
        for hyp in HYPOTHESES:
            source = SOURCE_BY_HYP[hyp]
            cfg = replayer.HYP[hyp]
            previous_n = int((conn.execute("SELECT value FROM system_state WHERE key = ?",
                                           (STATE_KEY.format(hyp=hyp),)).fetchone() or {"value": "0"})["value"])
            previous_status = (conn.execute("SELECT value FROM system_state WHERE key = ?",
                                            (STATUS_KEY.format(hyp=hyp),)).fetchone() or {"value": "non établie (n insuffisant)"})["value"]

            live = load_live(conn, source, replayer.DATA_CUTOFF)
            window_start = min((t.entry_time_utc for t in live), default=None)
            backtest_trades = []
            if window_start is not None:
                for asset in replayer.ASSETS:
                    backtest_trades += replayer.replay_period(cfg["module"], cfg["extras"], cfg["donchian"], asset,
                                                              window_start, replayer.DATA_CUTOFF, True, cfg["overrides"])
            backtest = [TradeRef(t.asset, t.direction, t.entry_time_utc, t.r_multiple_total) for t in backtest_trades]
            metrics = compute_fidelity_metrics(hyp, live, backtest)

            update = compute_update(hyp, previous_n, previous_status, metrics)
            summary_lines.append(format_monthly_summary_line(update, metrics))
            if update.just_crossed_20:
                message = format_first_n20_message(update, metrics)
                if send_notification(config.telegram_bot_token, config.telegram_chat_id, message):
                    print(message)
                else:
                    print(f"Envoi Telegram échoué pour {hyp} — nouvel essai le mois prochain, état non mis à jour")
                    continue  # ne persiste pas l'état si l'alerte n'est pas partie (nouvel essai)

            conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (STATE_KEY.format(hyp=hyp), str(metrics.n_paired), datetime.now().isoformat()),
            )
            conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (STATUS_KEY.format(hyp=hyp), update.current_status, datetime.now().isoformat()),
            )
            conn.commit()
        print("\n".join(summary_lines))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
