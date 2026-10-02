import hashlib
import hmac
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import ADMIN_PASSWORD, db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    mrn TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    date_of_birth TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    address TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    appointment_date TEXT NOT NULL,
    clinician_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    note TEXT NOT NULL,
    diagnosis TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bloodwork (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    appointment_id INTEGER REFERENCES appointments(id),
    collected_at TEXT NOT NULL,
    panel_name TEXT NOT NULL,
    analyte TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    ref_low REAL,
    ref_high REAL,
    flag TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL,
    pii_spans TEXT NOT NULL DEFAULT '[]',
    UNIQUE (source_table, source_id)
);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    client_id INTEGER REFERENCES clients(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    client_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appointments_client ON appointments(client_id);
CREATE INDEX IF NOT EXISTS idx_bloodwork_client ON bloodwork(client_id);
CREATE INDEX IF NOT EXISTS idx_chunks_client ON chunks(client_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 80_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 80_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def init_db() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'admin_password_hash'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('admin_password_hash', ?)",
                (hash_password(ADMIN_PASSWORD),),
            )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
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


def audit(conn: sqlite3.Connection, role: str, client_id: int | None, action: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO audit_log (created_at, role, client_id, action, detail)
           VALUES (?, ?, ?, ?, ?)""",
        (utc_now(), role, client_id, action, detail[:1000]),
    )


def create_session(conn: sqlite3.Connection, role: str, client_id: int | None) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, role, client_id, created_at) VALUES (?, ?, ?, ?)",
        (token, role, client_id, utc_now()),
    )
    return token


def get_session(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute(
        "SELECT token, role, client_id, created_at FROM sessions WHERE token = ?",
        (token,),
    ).fetchone()


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
