"""
db.py — Schéma et accès base de données (SQLite).

La base de données EST le projet : elle conditionne les décisions Go/No-Go
et l'historique de confiance par actif/source. À sauvegarder quotidiennement
hors du serveur (voir §7 du guide de démarrage P0).
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    asset TEXT,
    direction TEXT,
    entry_price REAL,
    stop_price REAL,
    take_profit_json TEXT,
    classification TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    units REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    pnl_eur REAL,
    environment TEXT NOT NULL DEFAULT 'demo',
    status TEXT NOT NULL DEFAULT 'open'
);

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

CREATE TABLE IF NOT EXISTS envelope_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    balance_after REAL NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS go_nogo_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason TEXT NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Ouvre une connexion SQLite, en créant le dossier parent si nécessaire."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Crée les tables si elles n'existent pas encore. Idempotent."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
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
