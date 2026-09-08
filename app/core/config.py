"""
SQLite-based persistent configuration and conversion log.
Database lives at %APPDATA%/MarkItDownGUI/state.db
"""
from __future__ import annotations

import os
import sqlite3
import threading
import hashlib
from pathlib import Path
from typing import Any

_DB_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MarkItDownGUI"
_DB_PATH = _DB_DIR / "state.db"
_KEYRING_SERVICE = "MarkItDownGUI"

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
    # Image descriptions are cached from conversion workers while the UI
    # reads settings and the conversion log on the main thread. SQLite
    # connections reject that by default, so allow cross-thread access and
    # serialize every operation with ``_db_lock`` below.
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
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
_db_lock = threading.RLock()


def get_db() -> sqlite3.Connection:
    global _conn
    with _db_lock:
        if _conn is None:
            _conn = _get_connection()
            _migrate(_conn)
        return _conn


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def _keyring_username() -> str:
    # Keep test/portable databases isolated while retaining a stable identity
    # for the normal %APPDATA% database.
    identity = os.path.normcase(os.path.abspath(str(_DB_PATH))).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    return f"llm_api_key:{digest}"


def _read_secure_api_key() -> str | None:
    try:
        import keyring

        return keyring.get_password(_KEYRING_SERVICE, _keyring_username())
    except Exception:
        return None


def _write_secure_api_key(value: str) -> bool:
    try:
        import keyring
        from keyring.errors import PasswordDeleteError

        username = _keyring_username()
        if value:
            keyring.set_password(_KEYRING_SERVICE, username, value)
        else:
            try:
                keyring.delete_password(_KEYRING_SERVICE, username)
            except PasswordDeleteError:
                pass
        return True
    except Exception:
        return False


def secure_storage_available() -> bool:
    """Whether an operational OS credential backend is available."""
    try:
        import keyring

        backend = keyring.get_keyring()
        return float(getattr(backend, "priority", 0)) > 0
    except Exception:
        return False

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
    with _db_lock:
        db = get_db()
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if key == "llm_api_key":
        secure_value = _read_secure_api_key()
        if secure_value is not None:
            return secure_value
        # One-time migration from older versions that stored the key in
        # SQLite. Keep the plaintext fallback only if the OS vault fails.
        plaintext = str(row["value"] or "") if row else ""
        if plaintext and _write_secure_api_key(plaintext):
            with _db_lock:
                db = get_db()
                db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '')",
                    (key,),
                )
                db.commit()
        return plaintext
    if row:
        return row["value"]
    return _DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    stored_value = str(value)
    if key == "llm_api_key" and _write_secure_api_key(stored_value):
        stored_value = ""
    with _db_lock:
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, stored_value),
        )
        db.commit()


def get_all_settings() -> dict[str, str]:
    with _db_lock:
        db = get_db()
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    result = dict(_DEFAULTS)
    result.update({r["key"]: r["value"] for r in rows})
    result["llm_api_key"] = get_setting("llm_api_key")
    return result


# ---------------------------------------------------------------------------
# Conversion log helpers
# ---------------------------------------------------------------------------


def log_start(source_path: str, started_at: str) -> int:
    with _db_lock:
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
    with _db_lock:
        db = get_db()
        db.execute(
            """UPDATE conversion_log
               SET output_path=?, finished_at=?, status=?, error=?
               WHERE id=?""",
            (output_path, finished_at, status, error, row_id),
        )
        db.commit()


def get_log_rows(limit: int = 500) -> list[dict[str, Any]]:
    with _db_lock:
        db = get_db()
        rows = db.execute(
            "SELECT * FROM conversion_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_output(source_path: str) -> str | None:
    """Return the newest successful output recorded for ``source_path``."""
    with _db_lock:
        db = get_db()
        row = db.execute(
            """SELECT output_path FROM conversion_log
               WHERE source_path=? AND status='ok' AND output_path IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (source_path,),
        ).fetchone()
    if not row:
        return None
    output_path = str(row["output_path"] or "").strip()
    return output_path or None


def clear_log() -> None:
    with _db_lock:
        db = get_db()
        db.execute("DELETE FROM conversion_log")
        db.commit()


# ---------------------------------------------------------------------------
# Seen-files helpers (for watcher deduplication across sessions)
# ---------------------------------------------------------------------------


def mark_seen(path: str, added_at: str) -> None:
    with _db_lock:
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO seen_files (path, added_at) VALUES (?, ?)",
            (path, added_at),
        )
        db.commit()


def is_seen(path: str) -> bool:
    with _db_lock:
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM seen_files WHERE path=?", (path,)
        ).fetchone()
    return row is not None


def close_db() -> None:
    global _conn
    with _db_lock:
        if _conn is not None:
            _conn.close()
            _conn = None


# ---------------------------------------------------------------------------
# Streaming path validation
# ---------------------------------------------------------------------------


def validate_streaming_paths(
    watch: str, output: str
) -> tuple[bool, str]:
    """
    Validate the watched/output folder pair for streaming mode.

    Rules:
        * Both must be non-empty.
        * Both must point to an existing directory on disk.
        * After `resolve()` (case-normalised on Windows), the two paths
          must not be identical and the output folder must not live
          INSIDE the watched tree. Output as an ANCESTOR of watched is
          allowed: this is how we keep `.md` files out of the watched
          tree so a freshly written `.md` never feeds another scan.

    Returns ``(ok, message)``. On success ``message`` is empty; on
    failure it is a human-readable Russian explanation suitable for a
    toast or inline error.
    """
    watch = (watch or "").strip()
    output = (output or "").strip()
    if not watch:
        return False, "Не выбрана папка сканирования."
    if not output:
        return False, (
            "Для стриминга нужно задать папку сохранения "
            "(она должна быть снаружи папки сканирования)."
        )
    if not os.path.isdir(watch):
        return False, f"Папка сканирования не найдена: {watch}"
    if not os.path.isdir(output):
        return False, f"Папка сохранения не найдена: {output}"
    try:
        # ``normcase`` is essential on Windows: without it, the same
        # directory written with different letter casing bypasses the loop
        # protection even though the filesystem treats both paths as equal.
        watch_r = os.path.normcase(os.path.realpath(os.path.abspath(watch)))
        output_r = os.path.normcase(os.path.realpath(os.path.abspath(output)))
    except OSError as exc:
        return False, f"Не удалось разобрать путь: {exc}"

    if watch_r == output_r:
        return False, (
            "Папка сохранения совпадает с папкой сканирования."
        )
    # If the watched tree lives INSIDE the output folder, that's fine
    # — .md results land above the watched root. The reverse (output
    # inside watched) is what we must forbid: a freshly written .md
    # would otherwise be picked up by the next scan and trigger another
    # conversion, producing an .md → .md_1 → … loop.
    output_prefix = output_r.rstrip(os.sep) + os.sep
    watch_r_prefixed = watch_r.rstrip(os.sep) + os.sep
    if output_prefix.startswith(watch_r_prefixed):
        return False, (
            "Папка сохранения находится внутри папки "
            "сканирования - это вызовет циклическую обработку "
            "(.md → .md_1 → ...). Выберите внешнюю папку."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Image-description cache helpers (v2)
# ---------------------------------------------------------------------------


def get_cached_description(hash_key: str, model: str, prompt: str) -> str | None:
    """Return a previously cached description for (hash, model, prompt), or None."""
    try:
        with _db_lock:
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
        with _db_lock:
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
