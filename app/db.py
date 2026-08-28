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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS responses (
    id TEXT PRIMARY KEY,
    stack_id TEXT NOT NULL REFERENCES stacks(id),
    quad_score REAL,
    finalized INTEGER NOT NULL DEFAULT 0,
    free_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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


def init_db() -> None:
    with get_db() as db:
        db.executescript(SCHEMA)
