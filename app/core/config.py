"""
SQLite-based persistent configuration and conversion log.
Database lives at %APPDATA%/MarkItDownGUI/state.db
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

_DB_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MarkItDownGUI"
_DB_PATH = _DB_DIR / "state.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS conversion_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_path TEXT NOT NULL,
        output_path TEXT,
        status      TEXT NOT NULL DEFAULT 'pending',
        started_at  TEXT,
        finished_at TEXT,
        error       TEXT
    );
    CREATE TABLE IF NOT EXISTS seen_files (
        path TEXT PRIMARY KEY,
        added_at TEXT
    );
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    );
    INSERT OR IGNORE INTO schema_version VALUES (1);
    """,
    # v2 — image-description cache
    """
    CREATE TABLE IF NOT EXISTS image_descriptions (
        hash        TEXT NOT NULL,
        model       TEXT NOT NULL,
        prompt      TEXT NOT NULL,
        description TEXT,
        created_at  TEXT,
        PRIMARY KEY (hash, model, prompt)
    );
    INSERT OR IGNORE INTO schema_version VALUES (2);
    """,
]

_CURRENT_VERSION = len(_MIGRATIONS)


def _get_connection() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    current = 0
    if row:
        r = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = r[0] or 0

    for i, sql in enumerate(_MIGRATIONS[current:], start=current + 1):
        conn.executescript(sql)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version VALUES (?)", (i,)
        )
    conn.commit()


# Singleton connection (lazy)
_conn: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _get_connection()
        _migrate(_conn)
    return _conn


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "theme": "system",
    "watch_folder": "",
    "output_folder": "",
    "accent_color": "",       # empty → use built-in default
    "accent_color_dark": "",  # override for dark theme; empty → derive
    "llm_api_key": "",
    "llm_base_url": "",
    "llm_model": "",
    "llm_prompt": "",
    "docintel_endpoint": "",
    "cu_endpoint": "",
    "cu_analyzer_id": "",
    "use_plugins": "0",
    "enable_docintel": "0",
    "enable_cu": "0",
    "enable_audio": "0",
    "enable_youtube": "0",
    "describe_images": "0",
    "streaming_mode": "0",
}


def get_setting(key: str) -> str:
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        return row["value"]
    return _DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    db.commit()


def get_all_settings() -> dict[str, str]:
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    result = dict(_DEFAULTS)
    result.update({r["key"]: r["value"] for r in rows})
    return result


# ---------------------------------------------------------------------------
# Conversion log helpers
# ---------------------------------------------------------------------------


def log_start(source_path: str, started_at: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO conversion_log (source_path, status, started_at) VALUES (?, 'running', ?)",
        (source_path, started_at),
    )
    db.commit()
    return cur.lastrowid  # type: ignore[return-value]


def log_finish(
    row_id: int,
    output_path: str,
    finished_at: str,
    status: str = "ok",
    error: str = "",
) -> None:
    db = get_db()
    db.execute(
        """UPDATE conversion_log
           SET output_path=?, finished_at=?, status=?, error=?
           WHERE id=?""",
        (output_path, finished_at, status, error, row_id),
    )
    db.commit()


def get_log_rows(limit: int = 500) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM conversion_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def clear_log() -> None:
    db = get_db()
    db.execute("DELETE FROM conversion_log")
    db.commit()


# ---------------------------------------------------------------------------
# Seen-files helpers (for watcher deduplication across sessions)
# ---------------------------------------------------------------------------


def mark_seen(path: str, added_at: str) -> None:
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO seen_files (path, added_at) VALUES (?, ?)",
        (path, added_at),
    )
    db.commit()


def is_seen(path: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM seen_files WHERE path=?", (path,)
    ).fetchone()
    return row is not None


def close_db() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ---------------------------------------------------------------------------
# Image-description cache helpers (v2)
# ---------------------------------------------------------------------------


def get_cached_description(hash_key: str, model: str, prompt: str) -> str | None:
    """Return a previously cached description for (hash, model, prompt), or None."""
    try:
        db = get_db()
        row = db.execute(
            "SELECT description FROM image_descriptions "
            "WHERE hash=? AND model=? AND prompt=?",
            (hash_key, model or "", prompt or ""),
        ).fetchone()
    except Exception:
        # Corrupted cache → behave as a miss.
        return None
    if not row:
        return None
    desc = row["description"]
    return desc if desc else None


def store_description(
    hash_key: str,
    model: str,
    prompt: str,
    description: str,
    created_at: str,
) -> None:
    """Persist a description into the cache. Errors are swallowed."""
    try:
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO image_descriptions "
            "(hash, model, prompt, description, created_at) VALUES (?, ?, ?, ?, ?)",
            (hash_key, model or "", prompt or "", description or "", created_at),
        )
        db.commit()
    except Exception:
        # Best-effort cache write; never raise to the caller.
        pass
