"""Contrôle quotidien de surveillance des hypothèses (29/10/2026) — code
déterministe, sans LLM, sans accès broker. Règles : docs/regles_surveillance_2026-10-29.md.
Écrit les suspensions (jamais levées par ce script), une ligne de journal par
suspension, et l'état lisible data/surveillance/etat_AAAA-MM-JJ.md.
Cron VPS : 30 6 * * *   cd ... && venv/bin/python scripts/controle_surveillance.py"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.hypothesis_asset_map import entry_assets_for  # noqa: E402
from src import surveillance  # noqa: E402


def main(db_path: str = None, out_dir: str = "data/surveillance") -> int:
    db_path = db_path or load_config().db_path
    now = datetime.now(timezone.utc)
    verdicts, newly, trades = surveillance.run_daily_check(db_path, now.isoformat())
    entry_assets = {h: entry_assets_for(h) for h in surveillance.HYPOTHESIS_SOURCES}
    text = surveillance.format_state(
        verdicts, trades, now.strftime("%Y-%m-%d"), surveillance.epoch_start(db_path), entry_assets,
    )
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"etat_{now.strftime('%Y-%m-%d')}.md"
    path.write_text(text, encoding="utf-8")
    print(f"{now.isoformat()} état écrit : {path} ; nouvelles suspensions : {newly or 'aucune'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
