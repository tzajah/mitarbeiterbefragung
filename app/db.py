"""SQLite-Persistenz. Bewusst schlank (kein ORM) und anonymitätsbewusst:
Antworten hängen an zufällige UUIDs, es gibt KEINE Personentabelle."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS stacks (
    id TEXT PRIMARY KEY,
    department TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    spec_hash TEXT
);
CREATE TABLE IF NOT EXISTS responses (
    id TEXT PRIMARY KEY,
    stack_id TEXT REFERENCES stacks(id),   -- NULL = noch keinem Stapel zugeordnet
    quad_score REAL,
    finalized INTEGER NOT NULL DEFAULT 0,
    free_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    spec_hash TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    response_id TEXT NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    value TEXT,
    confidence REAL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    source TEXT NOT NULL DEFAULT 'auto',
    PRIMARY KEY (response_id, question_id)
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # Server-Betrieb: WAL erlaubt parallele Leser neben dem Schreiber
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cols(db, table: str) -> dict[str, sqlite3.Row]:
    return {r["name"]: r for r in db.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    """Legt das Schema an und migriert Altbestände (idempotent)."""
    with get_db() as db:
        db.executescript(SCHEMA)  # bestehende Tabellen bleiben unverändert
        if "spec_hash" not in _cols(db, "stacks"):
            db.execute("ALTER TABLE stacks ADD COLUMN spec_hash TEXT")
        cols = _cols(db, "responses")
        if cols["stack_id"]["notnull"]:
            # SQLite kann Constraints nicht ändern → Tabelle neu aufbauen.
            # FKs aus (answers → responses ON DELETE CASCADE), sonst löscht DROP die Antworten.
            keep = ", ".join(c for c in cols if c != "spec_hash")
            db.execute("PRAGMA foreign_keys = OFF")
            db.executescript(f"""
                BEGIN;
                CREATE TABLE responses_new (
                    id TEXT PRIMARY KEY,
                    stack_id TEXT REFERENCES stacks(id),
                    quad_score REAL,
                    finalized INTEGER NOT NULL DEFAULT 0,
                    free_text TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    spec_hash TEXT
                );
                INSERT INTO responses_new ({keep}) SELECT {keep} FROM responses;
                DROP TABLE responses;
                ALTER TABLE responses_new RENAME TO responses;
                COMMIT;""")
            db.execute("PRAGMA foreign_keys = ON")
        elif "spec_hash" not in cols:
            db.execute("ALTER TABLE responses ADD COLUMN spec_hash TEXT")
