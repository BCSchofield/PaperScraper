"""SQLite-backed persistence for search history and app settings."""

import sqlite3
import json
import os
from pathlib import Path
from typing import Optional

_DB_PATH = Path.home() / ".paperscraper" / "data.db"


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Remove any previously persisted API token (security cleanup)
        conn.execute("DELETE FROM settings WHERE key = 'hf_token'")
        conn.commit()


def add_search_term(term: str) -> None:
    with _connect() as conn:
        # Remove duplicate then re-insert to bubble it to the top
        conn.execute("DELETE FROM search_history WHERE term = ?", (term,))
        conn.execute("INSERT INTO search_history (term) VALUES (?)", (term,))
        conn.commit()


def get_search_history(limit: int = 30) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT term FROM search_history ORDER BY used_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [r["term"] for r in rows]


def delete_search_term(term: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM search_history WHERE term = ?", (term,))
        conn.commit()


def save_setting(key: str, value) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        conn.commit()


def load_setting(key: str, default=None):
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return default
    return json.loads(row["value"])
