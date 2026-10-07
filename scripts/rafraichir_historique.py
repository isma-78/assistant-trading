"""
rafraichir_historique.py — Étape 1 du mandat autonome du 08/10/2026
(Partie 1), docs/PROTOCOLE_AUTONOME_08-10.md §2.

Actifs/résolutions dérivés de la configuration RÉELLEMENT déployée de
H1-H4 (jamais une liste supposée) : listes d'actifs importées directement
des exécuteurs (`HYPOTHESIS_ASSETS`/`HYPOTHESIS2_ASSETS`/
`HYPOTHESIS3_ASSETS`/`HYPOTHESIS4_ASSETS`), overrides de résolution H3/H4
lus en base via `hypothesis_params.get_resolution_override` (jamais
supposés HOUR — vérifiés, repli codé HOUR si aucun override actif).

Garde-fous :
- démo vérifiée PAR CODE (`config.capital_environment == "demo"`) avant
  tout appel broker ;
- fenêtre scellée 2023-01-01 -> 2024-06-14 : assertion qu'aucune bougie
  fusionnée ne s'y trouve, et que la dernière bougie locale déjà présente
  est postérieure à cette fenêtre avant de lancer quoi que ce soit ;
- rien avant 2019-01-01 (assertion) ;
- fusion STRICTEMENT append-only : aucune bougie déjà présente n'est
  jamais remplacée, seules des bougies postérieures à la dernière déjà
  locale sont ajoutées. Écriture atomique (.tmp puis rename) ; un arrêt
  en cours de route laisse le fichier précédent intact ;
- contrôle d'intégrité par chevauchement (2 jours avant la dernière
  bougie locale, jamais fusionné, utilisé seulement pour comparer) :
  écart bid/ask médian toléré = 1 tick de l'actif
  (`data/instrument_specs.json`, repli documenté pour CHFJPY, absent de
  ce fichier). Dépassement -> rafraîchissement de CET actif/résolution
  ABANDONNÉ, fichier existant intact ;
- budget strict de 200 appels, espacement >= 3 s, arrêt immédiat au
  premier 429 (le nôtre ou celui des 6 exécuteurs live partagés) ;
- journal horodaté UTC, aucune valeur sensible.

Jamais de bougie en formation (une bougie n'est ajoutée que si close :
début + durée <= maintenant), même garde que refresh_historical_tail.py.
"""

import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capital_client import CapitalApiError, CapitalClient  # noqa: E402
from src.config import load_config  # noqa: E402
from src.hypothesis2_executor import HYPOTHESIS2_ASSETS as H2_ASSETS  # noqa: E402
from src.hypothesis3_executor import HYPOTHESIS3_ASSETS as H3_ASSETS  # noqa: E402
from src.hypothesis4_executor import HYPOTHESIS4_ASSETS as H4_ASSETS  # noqa: E402
from src.hypothesis_params import get_resolution_override  # noqa: E402
from src.retry import retry_with_backoff  # noqa: E402
from src.trend_executor import HYPOTHESIS_ASSETS as H1_ASSETS  # noqa: E402

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = ROOT / "data" / "historical"
INSTRUMENT_SPECS_PATH = ROOT / "data" / "instrument_specs.json"

RESOLUTION_STEP = {"HOUR": timedelta(hours=1), "HOUR_4": timedelta(hours=4), "DAY": timedelta(days=1)}
MAX_BARS_PER_REQUEST = 1000
MIN_PAUSE_SECONDS = 3.0
MAX_CALLS = 200
OVERLAP_DAYS = 2
SEALED_WINDOW_START = datetime(2023, 1, 1)
SEALED_WINDOW_END = datetime(2024, 6, 15)  # exclusif : la fenêtre scellée couvre jusqu'au 2024-06-14 inclus
MIN_ALLOWED_DATE = datetime(2019, 1, 1)
_RATE_LIMIT_MARKERS = ("too-many.requests", "429")

# Résolutions de confirmation croisée de H2 (hypothesis2_executor.py,
# `extra_resolutions=["HOUR_4", "DAY"]`) — valeur de CODE, pas un
# override DB ; H2_ASSETS importé, celle-ci reprise telle quelle faute
# de constante exportée par le module à ce jour (consigné dans
# docs/AUTONOMIE_08-10.md).
H2_EXTRA_RESOLUTIONS = ("HOUR_4", "DAY")

# CHFJPY : absent de data/instrument_specs.json (whitelist étendue le
# 28/08/2026, après la dernière extraction discover_instruments.py du
# 16/08/2026, jamais relancée depuis). Coté en JPY comme USDJPY (même
# convention de conversion, src/asset_whitelist.py) -> repli documenté
# sur le tick USDJPY plutôt qu'une valeur inventée.
_CHFJPY_TICK_FALLBACK_FROM = "USDJPY"
_CHFJPY_EPIC = "CHFJPY"


class RateLimitAbort(Exception):
    """429 — sur notre propre requête ou détecté côté exécuteurs live."""


class BudgetExhausted(Exception):
    """Budget de 200 appels épuisé — arrêt propre, reprise possible."""


class SealedWindowViolation(Exception):
    """Garde défensive : la fenêtre scellée 2023-01-01->2024-06-14 a été touchée."""


@dataclass
class CallBudget:
    max_calls: int
    used: int = 0

    def consume(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExhausted(f"budget d'appels épuisé ({self.max_calls})")
        self.used += 1


@dataclass
class RefreshResult:
    asset: str
    resolution: str
    status: str  # "ok" | "abandoned_integrity" | "up_to_date" | "error"
    bougies_avant: int
    bougies_apres: int
    appels_consommes: int
    detail: str = ""


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", ""))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_deployed_combinations(db_path: str) -> List[Tuple[str, str]]:
    """Union des (actif, résolution) réellement nécessaires à H1-H4 tel
    que RÉELLEMENT déployé. H1 : HOUR fixe. H3/H4 : override lu en base
    (`rule_changes`, statut='applique'), repli codé HOUR si absent —
    vérifié, jamais supposé. H2 : HOUR (entrée) + HOUR_4/DAY
    (confirmation croisée)."""
    h3_resolution = get_resolution_override(db_path, "H3_v2", "entree", "HOUR")
    h4_resolution = get_resolution_override(db_path, "H4_v2", "entree", "HOUR")
    combos = set()
    for asset in H1_ASSETS:
        combos.add((asset, "HOUR"))
    for asset in H3_ASSETS:
        combos.add((asset, h3_resolution))
    for asset in H4_ASSETS:
        combos.add((asset, h4_resolution))
    for asset in H2_ASSETS:
        combos.add((asset, "HOUR"))
        for res in H2_EXTRA_RESOLUTIONS:
            combos.add((asset, res))
    return sorted(combos)


def load_tick_sizes(specs_path: Path = INSTRUMENT_SPECS_PATH) -> Dict[str, float]:
    """Taille de tick par epic (`minStepDistance_value`), avec repli
    documenté pour CHFJPY (absent de ce fichier, voir commentaire module)."""
    raw = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    sizes: Dict[str, float] = {}
    for entry in raw.get("assets", {}).values():
        primary = entry.get("primary") or {}
        epic = primary.get("epic")
        tick = primary.get("minStepDistance_value")
        if epic and tick:
            sizes[epic] = float(tick)
    if _CHFJPY_EPIC not in sizes and _CHFJPY_TICK_FALLBACK_FROM in sizes:
        sizes[_CHFJPY_EPIC] = sizes[_CHFJPY_TICK_FALLBACK_FROM]
    return sizes


def executors_rate_limited(db_path: str, since_iso: str) -> str:
    """Les 6 exécuteurs live partagent le débit API — leur 429 passe
    toujours avant ce script. Retourne une raison non vide si un signe
    de rate-limit est apparu depuis le démarrage de ce script."""
    conn = sqlite3.connect(db_path)
    try:
        streak = conn.execute(
            "SELECT key, value FROM system_state WHERE key LIKE 'api_error_streak:%' "
            "AND updated_at >= ? AND CAST(value AS INTEGER) > 0",
            (since_iso,),
        ).fetchall()
        rejected = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE annulation_motif = 'rate_limit_429' AND ouvert_at >= ?",
            (since_iso,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()
    if streak:
        return f"compteur d'erreurs API > 0 côté exécuteurs : {len(streak)} clé(s)"
    if rejected:
        return f"{rejected} placement(s) annulé(s) pour 429 côté exécuteurs"
    return ""


def _spread(point: dict) -> Optional[float]:
    close = point.get("closePrice") or {}
    bid, ask = close.get("bid"), close.get("ask")
    if bid is None or ask is None:
        return None
    return ask - bid


def check_overlap_integrity(existing: List[dict], overlap_fresh: List[dict], tick_size: float) -> Tuple[bool, dict]:
    """Compare les bougies fraîchement récupérées sur la fenêtre de
    chevauchement (déjà présentes localement) à leur version locale.
    Écart bid/ask médian toléré : 1 tick. Aucune bougie commune ->
    contrôle réussi par défaut (rien à comparer, jamais un blocage pour
    une absence de donnée)."""
    existing_by_ts = {p.get("snapshotTimeUTC"): p for p in existing if p.get("snapshotTimeUTC")}
    diffs = []
    for point in overlap_fresh:
        ts = point.get("snapshotTimeUTC")
        local = existing_by_ts.get(ts)
        if local is None:
            continue
        old_spread, new_spread = _spread(local), _spread(point)
        if old_spread is not None and new_spread is not None:
            diffs.append(abs(new_spread - old_spread))
    if not diffs:
        return True, {"n_overlap": 0, "median_diff": None, "tolerance": tick_size}
    diffs.sort()
    mid = len(diffs) // 2
    median = diffs[mid] if len(diffs) % 2 else (diffs[mid - 1] + diffs[mid]) / 2
    return median <= tick_size, {"n_overlap": len(diffs), "median_diff": median, "tolerance": tick_size}


def fetch_window(
    client: CapitalClient,
    epic: str,
    resolution: str,
    start: datetime,
    end: datetime,
    budget: CallBudget,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> List[dict]:
    """Récupère toutes les bougies de [start, end[ par pages de
    MAX_BARS_PER_REQUEST, espacées d'au moins MIN_PAUSE_SECONDS,
    décomptées sur `budget`. Arrêt immédiat (RateLimitAbort) au premier
    429, propagation de toute autre erreur."""
    step = RESOLUTION_STEP[resolution]
    points: List[dict] = []
    cursor = start
    first_call = True
    while cursor < end:
        if not first_call:
            sleep_fn(MIN_PAUSE_SECONDS)
        first_call = False
        window_end = min(cursor + step * MAX_BARS_PER_REQUEST, end)
        budget.consume()
        params = {
            "resolution": resolution,
            "max": MAX_BARS_PER_REQUEST,
            "from": cursor.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": window_end.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            resp = retry_with_backoff(
                lambda p=params: client.get(f"/prices/{epic}", params=p),
                exceptions=(CapitalApiError, requests.exceptions.RequestException),
            )
        except CapitalApiError as exc:
            text = str(exc)
            if any(marker in text for marker in _RATE_LIMIT_MARKERS):
                raise RateLimitAbort(f"429 sur notre propre requête {epic}/{resolution}") from exc
            if "error.prices.not-found" in text:
                break
            raise
        points.extend(resp.get("prices", []))
        cursor = window_end
    return points


def _new_rows(existing: List[dict], fresh: List[dict], last: datetime, step: timedelta, now: datetime) -> List[dict]:
    """Les seules bougies candidates à un ajout : postérieures à `last`,
    déjà closes (début + durée <= now), absentes de `existing`. Jamais
    les bougies déjà présentes (potentiellement antérieures à
    2019-01-01 par construction des fichiers bruts — hors périmètre des
    gardes 2019/fenêtre scellée, qui ne portent QUE sur ce qui est
    ajouté ici, jamais sur l'historique déjà présent)."""
    existing_ts = {p.get("snapshotTimeUTC") for p in existing}
    appended = [
        p
        for p in fresh
        if p.get("snapshotTimeUTC")
        and p["snapshotTimeUTC"] not in existing_ts
        and _parse(p["snapshotTimeUTC"]) > last
        and _parse(p["snapshotTimeUTC"]) + step <= now
    ]
    return sorted(appended, key=lambda p: p["snapshotTimeUTC"])


def merge(existing: List[dict], fresh: List[dict], last: datetime, step: timedelta, now: datetime) -> List[dict]:
    """Fusion STRICTEMENT append-only : `existing` n'est jamais modifié
    ni remplacé, seules les nouvelles bougies (`_new_rows`) sont ajoutées."""
    return existing + _new_rows(existing, fresh, last, step, now)


def refresh_combination(
    client: CapitalClient,
    asset: str,
    resolution: str,
    now: datetime,
    budget: CallBudget,
    tick_sizes: Dict[str, float],
    historical_dir: Path = HISTORICAL_DIR,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> RefreshResult:
    path = historical_dir / f"{asset}_{resolution}.json"
    existing = json.loads(path.read_text(encoding="utf-8"))
    last = max(_parse(p["snapshotTimeUTC"]) for p in existing if p.get("snapshotTimeUTC"))
    if last < SEALED_WINDOW_END:
        raise SealedWindowViolation(
            f"{asset}/{resolution} : dernière bougie locale ({last}) antérieure à la fin de la fenêtre scellée"
        )
    step = RESOLUTION_STEP[resolution]
    overlap_start = max(last - timedelta(days=OVERLAP_DAYS), MIN_ALLOWED_DATE)
    calls_before = budget.used

    fresh = fetch_window(client, asset, resolution, overlap_start, now, budget, sleep_fn)
    overlap_fresh = [p for p in fresh if p.get("snapshotTimeUTC") and _parse(p["snapshotTimeUTC"]) <= last]

    tick = tick_sizes.get(asset)
    if tick is None:
        return RefreshResult(asset, resolution, "error", len(existing), len(existing), budget.used - calls_before,
                              "taille de tick inconnue pour cet actif — rafraîchissement abandonné par prudence")

    ok, detail = check_overlap_integrity(existing, overlap_fresh, tick)
    if not ok:
        return RefreshResult(asset, resolution, "abandoned_integrity", len(existing), len(existing),
                              budget.used - calls_before, str(detail))

    appended = _new_rows(existing, fresh, last, step, now)

    assert all(
        _parse(p["snapshotTimeUTC"]) >= MIN_ALLOWED_DATE for p in appended
    ), "bougie antérieure au 2019-01-01 détectée dans les données ajoutées — jamais écrite"
    assert not any(
        SEALED_WINDOW_START <= _parse(p["snapshotTimeUTC"]) < SEALED_WINDOW_END for p in appended
    ), "fenêtre scellée touchée par les données ajoutées — jamais écrite"

    merged = existing + appended
    if len(merged) == len(existing):
        return RefreshResult(asset, resolution, "up_to_date", len(existing), len(merged),
                              budget.used - calls_before, str(detail))

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged), encoding="utf-8")
    tmp.replace(path)
    return RefreshResult(asset, resolution, "ok", len(existing), len(merged), budget.used - calls_before, str(detail))


def _print_resume(remaining: List[Tuple[str, str]]) -> None:
    print(f"{_utcnow_iso()} Combinaisons restantes ({len(remaining)}) : {remaining}")
    print(
        f"{_utcnow_iso()} Reprise : relancer `python scripts/rafraichir_historique.py` "
        "— idempotent, les combinaisons déjà écrites ne consomment plus d'appel utile."
    )


def main(
    config=None,
    client_factory: Optional[Callable[[object], CapitalClient]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    historical_dir: Optional[Path] = None,
) -> int:
    # Jamais un défaut de fonction lié à HISTORICAL_DIR au moment de la
    # définition (figerait la valeur avant tout test) — résolu ici, à
    # l'appel, pour que l'injection reste possible.
    historical_dir = Path(historical_dir) if historical_dir is not None else HISTORICAL_DIR
    config = config or load_config()
    if config.capital_environment != "demo":
        print(f"{_utcnow_iso()} REFUS : CAPITAL_ENVIRONMENT n'est pas 'demo' — aucun appel broker.")
        return 1

    started = datetime.now(timezone.utc)
    started_iso = started.isoformat()
    now = started.replace(tzinfo=None)

    combos = get_deployed_combinations(config.db_path)
    print(f"{_utcnow_iso()} {len(combos)} combinaison(s) (actif, résolution) à rafraîchir, budget {MAX_CALLS} appels.")

    tick_sizes = load_tick_sizes()

    if client_factory is None:
        client = CapitalClient(  # pragma: no cover — vrai client réseau, jamais exercé par les tests (broker mocké)
            config.capital_api_key, config.capital_identifier, config.capital_api_password, DEMO_BASE_URL
        )
    else:
        client = client_factory(config)
    client.login()
    client.switch_account(config.capital_account_id)

    budget = CallBudget(MAX_CALLS)
    results: List[RefreshResult] = []

    for index, (asset, resolution) in enumerate(combos):
        reason = executors_rate_limited(config.db_path, started_iso)
        if reason:
            print(f"{_utcnow_iso()} ARRÊT IMMÉDIAT : {reason}")
            _print_resume(combos[index:])
            return 2
        try:
            result = refresh_combination(client, asset, resolution, now, budget, tick_sizes,
                                          historical_dir=historical_dir, sleep_fn=sleep_fn)
        except RateLimitAbort as exc:
            print(f"{_utcnow_iso()} ARRÊT IMMÉDIAT (429) : {exc}")
            _print_resume(combos[index:])
            return 2
        except BudgetExhausted as exc:
            print(f"{_utcnow_iso()} BUDGET ÉPUISÉ : {exc}")
            _print_resume(combos[index:])
            return 3
        results.append(result)
        print(
            f"{_utcnow_iso()} {asset}/{resolution} : {result.status} "
            f"{result.bougies_avant}->{result.bougies_apres} bougies, {result.appels_consommes} appel(s)"
        )

    print(f"{_utcnow_iso()} Terminé. {budget.used}/{MAX_CALLS} appels consommés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
