"""
SQLite persistence layer.

Design notes
------------
* One short-lived connection per operation. SQLite connections are not
  shareable across threads, and this app has three writers: the Qt main thread,
  the notifier thread, and the mobile-bridge server thread. A connection per
  call removes a whole class of "SQLite objects created in a thread can only be
  used in that same thread" bugs.
* WAL mode so the notifier can read while the UI writes.
* Every value that reaches SQL goes through a parameter placeholder.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from core import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    case_number  TEXT    DEFAULT '',
    national_id  TEXT    DEFAULT '',
    phone        TEXT    DEFAULT '',
    notes        TEXT    DEFAULT '',
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title        TEXT    DEFAULT '',
    session_date TEXT    NOT NULL,          -- ISO-8601 'YYYY-MM-DD HH:MM'
    court        TEXT    DEFAULT '',
    kind         TEXT    NOT NULL DEFAULT 'session',  -- session | deadline
    notes        TEXT    DEFAULT '',
    reminded_at  TEXT,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    filename     TEXT    NOT NULL,
    rel_path     TEXT    NOT NULL,
    category     TEXT    NOT NULL DEFAULT 'scans',
    source       TEXT    NOT NULL DEFAULT 'import',   -- import | mobile | scan
    ocr_done     INTEGER NOT NULL DEFAULT 0,
    added_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_date   ON sessions(session_date);
CREATE INDEX IF NOT EXISTS idx_sessions_client ON sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_docs_client     ON documents(client_id);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a configured connection, committing on clean exit."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and enable WAL. Call once at startup."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode = WAL")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def add_client(
    name: str,
    case_number: str = "",
    national_id: str = "",
    phone: str = "",
    notes: str = "",
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO clients (name, case_number, national_id, phone, notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), case_number.strip(), national_id.strip(),
             phone.strip(), notes.strip(), _now()),
        )
        return int(cur.lastrowid)


def list_clients(search: str = "") -> list[sqlite3.Row]:
    with connect() as conn:
        if search.strip():
            like = f"%{search.strip()}%"
            return conn.execute(
                "SELECT * FROM clients"
                " WHERE name LIKE ? OR case_number LIKE ? OR national_id LIKE ?"
                " ORDER BY name COLLATE NOCASE",
                (like, like, like),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM clients ORDER BY name COLLATE NOCASE"
        ).fetchall()


def get_client(client_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()


def update_client(client_id: int, **fields: Any) -> None:
    allowed = {"name", "case_number", "national_id", "phone", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    clause = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(
            f"UPDATE clients SET {clause} WHERE id = ?",
            (*updates.values(), client_id),
        )


def delete_client(client_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))


# ---------------------------------------------------------------------------
# Court sessions and deadlines
# ---------------------------------------------------------------------------

def add_session(
    client_id: int,
    session_date: str,
    title: str = "",
    court: str = "",
    kind: str = "session",
    notes: str = "",
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (client_id, title, session_date, court, kind, notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, title, session_date, court, kind, notes, _now()),
        )
        return int(cur.lastrowid)


def sessions_between(start: date, end: date) -> list[sqlite3.Row]:
    """Sessions with a client name joined in, ordered by date."""
    with connect() as conn:
        return conn.execute(
            "SELECT s.*, c.name AS client_name FROM sessions s"
            " JOIN clients c ON c.id = s.client_id"
            " WHERE date(s.session_date) BETWEEN ? AND ?"
            " ORDER BY s.session_date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


def upcoming_sessions(days: int) -> list[sqlite3.Row]:
    today = date.today()
    return sessions_between(today, today + timedelta(days=days))


def due_reminders(days: int, now: datetime | None = None) -> list[sqlite3.Row]:
    """Sessions inside the lookahead window that have not been reminded yet."""
    now = now or datetime.now()
    today = now.date()
    with connect() as conn:
        return conn.execute(
            "SELECT s.*, c.name AS client_name FROM sessions s"
            " JOIN clients c ON c.id = s.client_id"
            " WHERE s.reminded_at IS NULL"
            "   AND date(s.session_date) <= date(?)"
            "   AND datetime(s.session_date) >= datetime(?)"
            " ORDER BY s.session_date",
            ((today + timedelta(days=days)).isoformat(),
             now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()),
        ).fetchall()


def mark_reminded(session_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE sessions SET reminded_at = ? WHERE id = ?", (_now(), session_id)
        )


def delete_session(session_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def add_document(
    client_id: int,
    filename: str,
    rel_path: str,
    category: str = config.DEFAULT_CATEGORY,
    source: str = "import",
    ocr_done: bool = False,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents (client_id, filename, rel_path, category, source, ocr_done, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, filename, rel_path, category, source,
             int(ocr_done), _now()),
        )
        return int(cur.lastrowid)


def list_documents(client_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE client_id = ? ORDER BY added_at DESC",
            (client_id,),
        ).fetchall()


def find_document(
    client_id: int, filename: str, category: str | None = None
) -> sqlite3.Row | None:
    """Locate a stored document by client + filename (category narrows first).

    Used to resolve a RAG retrieval source back to the file on disk so the chat
    panel can offer a clickable link. Returns the most recently added match, or
    None. Every value is parameterised.
    """
    with connect() as conn:
        if category:
            row = conn.execute(
                "SELECT * FROM documents"
                " WHERE client_id = ? AND filename = ? AND category = ?"
                " ORDER BY added_at DESC LIMIT 1",
                (client_id, filename, category),
            ).fetchone()
            if row is not None:
                return row
        return conn.execute(
            "SELECT * FROM documents"
            " WHERE client_id = ? AND filename = ?"
            " ORDER BY added_at DESC LIMIT 1",
            (client_id, filename),
        ).fetchone()


def mark_document_ocr(document_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE documents SET ocr_done = 1 WHERE id = ?", (document_id,)
        )


def get_document(document_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()


def delete_document(document_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------

def counts() -> dict[str, int]:
    with connect() as conn:
        return {
            "clients": conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "sessions": conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE date(session_date) >= date('now')"
            ).fetchone()[0],
        }
