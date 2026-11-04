"""
QualityShare Database Models

Uses plain SQLite via Python's built-in sqlite3 module.
Two tables:
  - shares: a group of files shared under one QR code / link
  - files:  individual files belonging to a share
"""

import sqlite3
import os
from app.config import Config


def get_db():
    """
    Open a connection to the SQLite database.

    Returns a connection with Row factory so results act like dictionaries.
    The caller must close the connection when done (use 'with' or try/finally).
    """
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row          # access columns by name
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")   # enforce FK constraints
    return conn


def init_db():
    """
    Create the database tables if they don't exist.
    Also handles safe schema migration for existing databases (e.g., adding user_id to shares).
    """
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name       TEXT    NOT NULL,
                username        TEXT    NOT NULL UNIQUE,
                email           TEXT    NOT NULL UNIQUE,
                password_hash   TEXT    NOT NULL,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS shares (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER,
                token           TEXT    NOT NULL UNIQUE,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                expires_at      TEXT,
                file_count      INTEGER NOT NULL DEFAULT 0,
                total_size      INTEGER NOT NULL DEFAULT 0,
                is_complete     INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS files (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                share_id        INTEGER NOT NULL,
                original_name   TEXT    NOT NULL,
                storage_key     TEXT    NOT NULL,
                content_type    TEXT    NOT NULL,
                size_bytes      INTEGER NOT NULL DEFAULT 0,
                file_type       TEXT    NOT NULL,
                upload_status   TEXT    NOT NULL DEFAULT 'pending',
                checksum        TEXT,
                upload_id       TEXT,
                chunks_received INTEGER NOT NULL DEFAULT 0,
                total_chunks    INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (share_id) REFERENCES shares(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_shares_token ON shares(token);
            CREATE INDEX IF NOT EXISTS idx_files_share_id ON files(share_id);
            CREATE INDEX IF NOT EXISTS idx_shares_expires_at ON shares(expires_at);
        """)
        conn.commit()

        # Check if user_id column exists in shares table (for migration of existing databases)
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "user_id" not in columns:
            conn.execute("ALTER TABLE shares ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;")
            conn.commit()

        conn.execute("CREATE INDEX IF NOT EXISTS idx_shares_user_id ON shares(user_id);")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helper functions for working with users, shares and files
# ---------------------------------------------------------------------------

def create_user(full_name, username, email, password_hash):
    """Insert a new user row and return their id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO users (full_name, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (full_name, username, email, password_hash),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_user_by_id(user_id):
    """Look up a user by ID. Returns a Row or None."""
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def get_user_by_username(username):
    """Look up a user by username. Returns a Row or None."""
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()


def get_user_by_email(email):
    """Look up a user by email. Returns a Row or None."""
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()


def create_share(token, expires_at=None, user_id=None):
    """Insert a new share row and return its id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO shares (token, expires_at, user_id) VALUES (?, ?, ?)",
            (token, expires_at, user_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_share_by_token(token):
    """Look up a share by its public token. Returns a Row or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM shares WHERE token = ?", (token,)
        ).fetchone()
        return row
    finally:
        conn.close()


def get_share_by_id(share_id):
    """Look up a share by its internal id. Returns a Row or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM shares WHERE id = ?", (share_id,)
        ).fetchone()
        return row
    finally:
        conn.close()


def update_share_complete(share_id, file_count, total_size):
    """Mark a share as fully uploaded."""
    conn = get_db()
    try:
        conn.execute(
            """UPDATE shares
               SET is_complete = 1, file_count = ?, total_size = ?
               WHERE id = ?""",
            (file_count, total_size, share_id),
        )
        conn.commit()
    finally:
        conn.close()


def create_file(share_id, original_name, storage_key, content_type,
                size_bytes, file_type, total_chunks, upload_id=None):
    """Insert a new file row and return its id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO files
               (share_id, original_name, storage_key, content_type,
                size_bytes, file_type, total_chunks, upload_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (share_id, original_name, storage_key, content_type,
             size_bytes, file_type, total_chunks, upload_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_file_chunk_progress(file_id, chunks_received):
    """Update how many chunks have been received for a file."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE files SET chunks_received = ? WHERE id = ?",
            (chunks_received, file_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_file_complete(file_id, checksum):
    """Mark a file upload as complete with its verified checksum."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE files SET upload_status = 'complete', checksum = ? WHERE id = ?",
            (checksum, file_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_file_failed(file_id):
    """Mark a file upload as failed."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE files SET upload_status = 'failed' WHERE id = ?",
            (file_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_files_for_share(share_id):
    """Return all files belonging to a share, ordered by id."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM files WHERE share_id = ? ORDER BY id",
            (share_id,),
        ).fetchall()
        return rows
    finally:
        conn.close()


def get_file_by_id(file_id):
    """Look up a single file by its id. Returns a Row or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        return row
    finally:
        conn.close()


def delete_expired_shares():
    """
    Delete shares whose expires_at is in the past.
    Returns the list of storage keys that should be removed from cloud storage.
    """
    conn = get_db()
    try:
        # Find storage keys for expired shares so we can delete them from S3
        rows = conn.execute(
            """SELECT f.storage_key FROM files f
               JOIN shares s ON f.share_id = s.id
               WHERE s.expires_at IS NOT NULL
                 AND s.expires_at < datetime('now')"""
        ).fetchall()
        keys = [row["storage_key"] for row in rows]

        # Delete the shares (files cascade)
        conn.execute(
            """DELETE FROM shares
               WHERE expires_at IS NOT NULL
                 AND expires_at < datetime('now')"""
        )
        conn.commit()
        return keys
    finally:
        conn.close()
