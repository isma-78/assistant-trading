"""
db.py — Schéma et accès base de données (SQLite).

Schéma aligné sur docs/CDC_v4.md §4.5, avec trois ajouts documentés dans
docs/DECISIONS.md (aucun ne modifie une table existante du CDC, aucun ne
touche un module critique déjà testé) :
- `matinale_summaries` : le CDC ne prévoit aucune table pour le résumé par
  actif d'une Matinale (biais du corps, tag Sentiment, contradiction) alors
  que §3.8 (variable #1) et §3.4 en dépendent directement. Absent du §4.5,
  ajouté ici.
- `suivi_events` : idem pour les messages de suivi (§3.2) — aucune table
  dédiée au CDC ; les rattacher aux trades (`trade_partials`) n'a de sens
  qu'à partir de P2 (executor). En attendant, archivés ici pour ne pas
  perdre d'historique (principe §3.8 : "l'historique a de la valeur").
- `risk_decisions` et `go_nogo_events` : hérités du palier P0, absents du
  §4.5 mais utiles à l'audit (invariant #5 : tout ordre journalisé) —
  conservés, aucun conflit avec le schéma CDC.

La base de données EST le projet : elle conditionne les décisions Go/No-Go
et l'historique de confiance par actif/source. À sauvegarder quotidiennement
hors du serveur (§5.2 du CDC — voir scripts/backup_and_sync.sh).
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
-- ---------------------------------------------------------------------
-- Ingestion (§4.4 telegram_listener, §4.5)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_msg_id INTEGER NOT NULL,
    reply_to_msg_id INTEGER,
    channel TEXT NOT NULL,
    received_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    message_type TEXT NOT NULL,   -- "matinale" | "signal" | "suivi" | "autre"
    processed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (channel, telegram_msg_id)
);

-- ---------------------------------------------------------------------
-- Signal (§3.2 : seul type exécutable, §4.5)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id INTEGER NOT NULL REFERENCES raw_messages(id),
    source TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'signal',
    actif TEXT,
    sens TEXT,                    -- "long" | "short"
    entree_min REAL,
    entree_max REAL,
    stop_loss REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    take_profit REAL,  -- ajout P2.9+ hors §4.5 : cible fixe unique (Hypothèse #4, retour à la moyenne,
                        -- voir docs/HYPOTHESES.md) — DÉLIBÉRÉMENT distincte de tp1/tp2/tp3 (mécanisme
                        -- Station X, 3 paliers + trailing) pour qu'aucun trade ne puisse ambiguïser les
                        -- deux dispatches de gestion de position (executor._evaluate_position_management)
    confiance REAL,
    statut TEXT NOT NULL DEFAULT 'a_valider',
    raison_rejet TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    bid REAL,
    ask REAL,
    spread REAL,
    atr REAL,
    ma_longue REAL,
    tendance_fond TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS macro_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime TEXT NOT NULL,
    devise TEXT NOT NULL,
    intitule TEXT NOT NULL,
    impact TEXT NOT NULL,          -- "fort" | "moyen" | "faible"
    source TEXT
);

-- ---------------------------------------------------------------------
-- Matinale — ajout hors CDC (voir docs/DECISIONS.md)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matinale_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id INTEGER NOT NULL REFERENCES raw_messages(id),
    raw_asset_mention TEXT NOT NULL,
    actif TEXT,
    biais_corps TEXT NOT NULL,     -- "haussier" | "baissier" | "neutre" | "indetermine"
    sentiment_tag TEXT,            -- "haussier" | "baissier" | "neutre" | NULL — "Sentiment X" ou "Biais X.", voir docs/DECISIONS.md (20/08/2026)
    contradiction_detectee INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    -- Colonnes ajoutées le 20/08/2026, calibrées sur un exemple réel (voir
    -- docs/DECISIONS.md) — toutes nullables, None si le texte ne les mentionne pas.
    prix_courant REAL,
    zone_depart_min REAL,
    zone_depart_max REAL,
    niveau_majeur REAL,
    fvg_haut REAL,
    fvg_bas REAL,
    fib_50 REAL,
    fib_618 REAL,
    fib_786 REAL
);

-- ---------------------------------------------------------------------
-- Suivi — ajout hors CDC (voir docs/DECISIONS.md)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suivi_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id INTEGER NOT NULL REFERENCES raw_messages(id),
    reply_to_msg_id INTEGER,
    event TEXT NOT NULL,           -- "sl_hit" | "tp1_hit" | "tp2_hit" | "tp3_hit" | "update"
    pips REAL,
    recorded_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Trades et capital (§2.3, §2.10, §4.5) — P2+
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    deal_id TEXT,                  -- ajout P2 hors §4.5 : id broker (Capital.com), voir docs/DECISIONS.md
    source TEXT NOT NULL,
    actif TEXT NOT NULL,
    mode TEXT NOT NULL,            -- "demo" | "reel"
    direction TEXT NOT NULL,       -- "long" | "short"
    taille_initiale REAL NOT NULL,
    prix_entree_prevu REAL,
    prix_entree_reel REAL,
    slippage_entree REAL,
    guaranteed_stop INTEGER NOT NULL DEFAULT 0,  -- ajout P2.5+ hors §4.5 : voir docs/DECISIONS.md (correctif update_position_stop)
    stop_loss_initial REAL NOT NULL,
    stop_loss_courant REAL NOT NULL,
    risque_eur REAL NOT NULL,
    pourcentage_risque_applique REAL NOT NULL,
    ouvert_at TEXT NOT NULL,
    ferme_at TEXT,
    r_multiple_total REAL,
    pnl_brut REAL,
    couts REAL,
    pnl_net REAL,
    statut TEXT NOT NULL DEFAULT 'ouvert',
    cloture_reason TEXT,  -- ajout 20/08/2026 hors §4.5 : "stop_initial" | "stop_breakeven" | "trailing" | "stop_urgence", voir docs/DECISIONS.md
    stop_elargi INTEGER NOT NULL DEFAULT 0,  -- ajout 20/08/2026 hors §4.5 : stop élargi au minimum garanti broker, voir docs/DECISIONS.md
    stop_origine_signal REAL,  -- stop tel qu'émis par le signal AVANT élargissement ; NULL si stop_elargi=0
    session_marche TEXT,  -- ajout 20/08/2026 hors §4.5 : "asie" | "europe" | "us" | "hors_session", collecte uniquement, voir docs/DECISIONS.md
    regime_type TEXT  -- ajout 23/08/2026 hors §4.5 : "ma200" | "structural_bos_choch", voir docs/DECISIONS.md
                       -- (bascule du régime de l'Hypothèse #2). NULL pour Station X (aucune notion de
                       -- régime de fond) et pour tout trade antérieur à cet ajout non rétro-rempli.
);

CREATE TABLE IF NOT EXISTS trade_partials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    palier TEXT NOT NULL,          -- "tp1" | "tp2" | "tp3" | "trailing" | "macro"
    fraction REAL NOT NULL,
    prix_sortie REAL NOT NULL,
    r_atteint REAL NOT NULL,
    motif TEXT,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_features (
    trade_id INTEGER PRIMARY KEY REFERENCES trades(id),
    align_matinale INTEGER,
    align_tendance_fond INTEGER,
    ratio_gain_risque_prevu REAL,
    proximite_macro INTEGER,
    volatilite_relative REAL
);

CREATE TABLE IF NOT EXISTS envelopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actif TEXT NOT NULL,
    mode TEXT NOT NULL,            -- "demo" | "reel"
    source TEXT NOT NULL DEFAULT 'stationx',  -- ajout P2.5 hors §4.5 : voir docs/DECISIONS.md (Flux B)
    capital_initial REAL NOT NULL,
    capital_courant REAL NOT NULL,
    nb_rechargements INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (actif, mode, source)
);

CREATE TABLE IF NOT EXISTS envelope_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id INTEGER NOT NULL REFERENCES envelopes(id),
    trade_id INTEGER REFERENCES trades(id),
    montant_avant REAL NOT NULL,
    montant_apres REAL NOT NULL,
    type_mouvement TEXT NOT NULL,  -- "init" | "trade_pnl" | "reload"
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reserve_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trades(id),
    montant_ajoute REAL NOT NULL,
    reserve_totale REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Score de confiance et allocation (§2.4, §2.5) — P2+/P4
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS confidence_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actif TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    nb_trades INTEGER NOT NULL,
    esperance REAL NOT NULL,
    facteur_echantillon REAL NOT NULL,
    facteur_stabilite REAL NOT NULL,
    score REAL NOT NULL,
    eligible INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actif TEXT NOT NULL,
    montant_alloue REAL NOT NULL,
    motif TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Revue post-trade et analyse causale (§3.10, §3.11) — P3+/P5
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS post_trade_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    faits_json TEXT NOT NULL,
    analyse_texte TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS causal_analysis_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    declencheur TEXT NOT NULL,
    trades_concernes_ids TEXT NOT NULL,
    contexte_json TEXT NOT NULL,
    categorie TEXT NOT NULL,       -- "anomalie_technique" | "evenement_marche" | "hypothese_pattern"
    analyse_texte TEXT NOT NULL,
    action_prise TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    enonce TEXT NOT NULL,
    justification_causale TEXT NOT NULL,
    fenetre_test_debut TEXT NOT NULL,
    nb_trades_test INTEGER,
    resultat_stat TEXT,
    statut TEXT NOT NULL DEFAULT 'en_attente',
    promue_variable INTEGER NOT NULL DEFAULT 0,
    decided_at TEXT
);

-- ---------------------------------------------------------------------
-- Métriques et ajustements de règles (§2.4, §3.8) — P3+
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actif TEXT NOT NULL,
    source TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    nb_trades INTEGER NOT NULL,
    esperance_r REAL NOT NULL,
    profit_factor REAL,
    drawdown_courant REAL,
    drawdown_max REAL,
    taux_reussite_indicatif REAL
);

CREATE TABLE IF NOT EXISTS rule_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_at TEXT NOT NULL,
    variable TEXT NOT NULL,
    constat_stat TEXT NOT NULL,
    ajustement_propose TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'propose',
    validated_at TEXT,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    module TEXT NOT NULL,
    message TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Hérités de P0, hors §4.5, conservés pour l'audit (voir docs/DECISIONS.md)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    decided_at TEXT NOT NULL,
    approved INTEGER NOT NULL,
    reason TEXT,
    detail TEXT,
    units REAL,
    risk_amount_eur REAL
);

CREATE TABLE IF NOT EXISTS go_nogo_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Analyse de trade — ajout P2, hors §4.5 (voir docs/DECISIONS.md).
-- Deux parties strictement séparées dans le même schéma pour qu'aucune
-- confusion ne soit possible entre les deux (invariant #9) : les
-- colonnes déterministes sont calculées par trade_analyzer.py sans LLM ;
-- resume_narratif est produit par un LLM à partir de CES valeurs déjà
-- calculées, jamais l'inverse, et ne doit jamais contenir de jugement.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    signal_id INTEGER REFERENCES signals(id),

    -- Partie déterministe (calculée, jamais par un LLM)
    r_multiple_realise REAL NOT NULL,
    denouement TEXT NOT NULL,            -- "sl_hit" | "tp1_hit" | "tp2_hit" | "tp3_hit" | "cloture_manuelle"
    duree_secondes INTEGER NOT NULL,
    ecart_signal_execution REAL,         -- prix_entree_reel - prix d'entrée du signal
    actif TEXT NOT NULL,
    confiance_signal REAL,
    heure_ouverture INTEGER NOT NULL,    -- 0-23, UTC
    jour_semaine INTEGER NOT NULL,       -- 0=lundi ... 6=dimanche
    boosted INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'stationx',  -- "stationx" | "hypothesis" — ajout P2.5, voir docs/DECISIONS.md

    -- Partie LLM (narrative uniquement — jamais un jugement, invariant #9)
    resume_narratif TEXT,
    resume_genere_at TEXT,
    resume_modele TEXT,

    created_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Coupe-circuits (§2.7) + pauses manuelles (§7.1) — ajout P2.6, hors
-- §4.5 littéral (voir docs/DECISIONS.md). Journal d'événements plutôt
-- qu'un simple flag : un déclenchement "reprise manuelle" (week_r,
-- drawdown_r, manual_pause, stop_urgence, api_errors, breadth) reste
-- actif tant qu'aucune ligne cleared_at n'existe pour lui, quel que soit
-- ce que le calcul en direct des R redonnerait ensuite (§2.7 : "Reprise
-- Manuelle" — jamais un recalcul qui repasserait sous le seuil tout
-- seul). "day_r" ne dépend jamais de cleared_at : il s'éteint seul au
-- changement de date (voir circuit_breaker_store.py).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,          -- "asset" | "global"
    actif TEXT,                   -- NULL si scope="global"
    source TEXT,                  -- NULL si scope="global" ou pause actif-large (toutes sources)
    breaker_type TEXT NOT NULL,   -- "day_r" | "week_r" | "drawdown_r" | "manual_pause" | "stop_urgence" | "api_errors" | "breadth" | "channel_inactive"
    triggered_at TEXT NOT NULL,
    r_value REAL,
    note TEXT,
    cleared_at TEXT,
    cleared_by TEXT
);

-- Compteurs/marqueurs techniques (streak d'erreurs API par process,
-- dernier événement stop_urgence déjà traité par process, etc.) —
-- persistés pour survivre à un redémarrage, jamais lus par un LLM.
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Ouvre une connexion SQLite, en créant le dossier parent si nécessaire."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# Colonnes ajoutées à une table du §4.5 après sa création initiale.
# CREATE TABLE IF NOT EXISTS ne migre jamais une table déjà existante —
# sans ceci, une base créée avant l'ajout de trades.deal_id (palier P2)
# resterait sans la colonne indéfiniment (bug réel trouvé le 16/08/2026
# en vérifiant l'état du VPS avant de démarrer executor.py : la table
# trades du palier P1 n'avait pas deal_id, la première écriture
# d'executor.open_signal() aurait échoué. Voir docs/DECISIONS.md).
_COLUMN_MIGRATIONS = [
    ("trades", "deal_id", "TEXT"),
    ("trade_analysis", "source", "TEXT NOT NULL DEFAULT 'stationx'"),
    ("trades", "guaranteed_stop", "INTEGER NOT NULL DEFAULT 0"),
    ("matinale_summaries", "prix_courant", "REAL"),
    ("matinale_summaries", "zone_depart_min", "REAL"),
    ("matinale_summaries", "zone_depart_max", "REAL"),
    ("matinale_summaries", "niveau_majeur", "REAL"),
    ("matinale_summaries", "fvg_haut", "REAL"),
    ("matinale_summaries", "fvg_bas", "REAL"),
    ("matinale_summaries", "fib_50", "REAL"),
    ("matinale_summaries", "fib_618", "REAL"),
    ("matinale_summaries", "fib_786", "REAL"),
    ("trades", "cloture_reason", "TEXT"),
    ("trades", "stop_elargi", "INTEGER NOT NULL DEFAULT 0"),
    ("trades", "stop_origine_signal", "REAL"),
    ("trades", "session_marche", "TEXT"),
    ("signals", "take_profit", "REAL"),
    ("trades", "regime_type", "TEXT"),
]


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")


# Sources hypothèse dont TOUS les trades existants, à la date de cette
# migration (23/08/2026), ont été ouverts sous un régime MA200 — jamais
# les constantes d'executor.py (importer executor.py depuis db.py créerait
# un import circulaire, executor.py important déjà connection_scope
# d'ici) : littéraux dupliqués, même convention que _KNOWN_HYPOTHESIS_
# SOURCES (executor.py/metrics.py/circuit_breaker_store.py/
# confidence_scorer.py). N'inclut PAS "hypothesis2" : ses trades
# ANTÉRIEURS à la bascule du régime (23/08/2026, voir docs/DECISIONS.md)
# doivent être étiquetés "ma200" malgré tout — traité séparément ci-dessous.
_MA200_ONLY_HYPOTHESIS_SOURCES = ("hypothesis", "hypothesis3", "hypothesis4")


def _backfill_regime_type(conn: sqlite3.Connection) -> None:
    """Rétro-remplit `trades.regime_type` pour les trades hypothèse déjà
    en base AVANT l'ajout de cette colonne (`ALTER TABLE ADD COLUMN` la
    laisse NULL pour toutes les lignes existantes, indéfiniment, sans ce
    backfill) — voir docs/DECISIONS.md, 23/08/2026 (bascule du régime de
    l'Hypothèse #2, MA200 -> structure BOS/CHoCH). Distingue deux cas :

    - H1/H3/H4 n'ont jamais changé de régime (toujours MA200) : tous
      leurs trades, passés et futurs, sont "ma200".
    - H2 a basculé : ses trades déjà en base à cette date ont TOUS été
      ouverts sous l'ANCIEN régime MA200 (aucun trade H2 structurel
      n'existe encore, la bascule et cette migration sont déployées dans
      le même changement) — donc rétro-remplis "ma200" comme les autres,
      pas "structural_bos_choch".

    Idempotent : ne cible que les lignes encore NULL. Tout futur trade
    (toute source, y compris un H2 post-bascule) écrit `regime_type` à
    l'ouverture (`executor.open_signal`), jamais NULL — ne peut donc plus
    jamais matcher cette condition une fois inséré, quelle que soit la
    fréquence d'appel de cette fonction (exécutée à chaque `init_db`,
    même patron que `_add_column_if_missing`).

    Garde-fou trouvé par les tests avant tout déploiement (voir
    docs/DECISIONS.md, 23/08/2026) : une table `trades` encore plus
    ancienne que celle visée par `_add_column_if_missing` (ex. un
    doublure de test antérieure à l'ajout même de `source`) ferait
    échouer la requête ci-dessous (`no such column: source`) — sans
    objet dans ce cas (aucune ligne ne peut référencer une source
    hypothèse sans cette colonne), donc silencieusement ignorée plutôt
    que de faire échouer `init_db()` en entier."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "source" not in existing:
        return
    sources = _MA200_ONLY_HYPOTHESIS_SOURCES + ("hypothesis2",)
    placeholders = ",".join("?" for _ in sources)
    conn.execute(
        f"UPDATE trades SET regime_type = 'ma200' WHERE regime_type IS NULL AND source IN ({placeholders})",
        sources,
    )


def _migrate_envelopes_source(conn: sqlite3.Connection) -> None:
    """`envelopes.source` (ajout P2.5, Flux B — voir docs/DECISIONS.md) ne
    peut pas être ajoutée par un simple ADD COLUMN : la contrainte
    UNIQUE(actif, mode) doit devenir UNIQUE(actif, mode, source), ce que
    SQLite ne permet pas de modifier sur une table existante. Reconstruit
    la table (les `id` sont préservés à l'identique pour que les
    références de `envelope_ledger` restent valides). Idempotent :
    ne s'exécute que si `source` n'existe pas déjà."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(envelopes)")}
    if "source" in existing:
        return

    # `envelope_ledger.envelope_id` référence `envelopes(id)` par clé
    # étrangère (§4.5). Avec PRAGMA foreign_keys=ON (get_connection),
    # SQLite refuse de DROP une table encore référencée comme parent par
    # une autre — trouvé en migrant la vraie base de production (VPS,
    # 19/08/2026), absent des tests locaux car leurs fixtures ne
    # journalisaient jamais de mouvement d'enveloppe avant migration. Le
    # pragma est un no-op au milieu d'une transaction implicite : on le
    # désactive et le réactive ici, en dehors de toute transaction
    # ouverte par le reste de init_db(), avec un commit explicite pour
    # que la table reconstruite soit visible avant réactivation.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "CREATE TABLE envelopes_new ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "actif TEXT NOT NULL,"
        "mode TEXT NOT NULL,"
        "source TEXT NOT NULL DEFAULT 'stationx',"
        "capital_initial REAL NOT NULL,"
        "capital_courant REAL NOT NULL,"
        "nb_rechargements INTEGER NOT NULL DEFAULT 0,"
        "created_at TEXT NOT NULL,"
        "updated_at TEXT NOT NULL,"
        "UNIQUE (actif, mode, source)"
        ")"
    )
    conn.execute(
        "INSERT INTO envelopes_new "
        "(id, actif, mode, source, capital_initial, capital_courant, nb_rechargements, created_at, updated_at) "
        "SELECT id, actif, mode, 'stationx', capital_initial, capital_courant, nb_rechargements, created_at, updated_at "
        "FROM envelopes"
    )
    conn.execute("DROP TABLE envelopes")
    conn.execute("ALTER TABLE envelopes_new RENAME TO envelopes")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def init_db(db_path: str) -> None:
    """Crée les tables si elles n'existent pas encore, puis applique les
    migrations connues (colonnes, contraintes) sur les tables déjà
    existantes. Idempotent dans les deux cas."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        for table, column, column_def in _COLUMN_MIGRATIONS:
            _add_column_if_missing(conn, table, column, column_def)
        _migrate_envelopes_source(conn)
        _backfill_regime_type(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection_scope(db_path: str):
    """Contexte transactionnel : commit si tout va bien, rollback sinon."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
