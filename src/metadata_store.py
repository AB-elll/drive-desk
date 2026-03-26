import json
import sqlite3
from datetime import datetime
from pathlib import Path


DB_PATH = Path(__file__).parent.parent / "drivedesk.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                file_id        TEXT PRIMARY KEY,
                file_name      TEXT NOT NULL,
                shared_at      DATETIME NOT NULL,
                primary_date   DATE,
                dates          TEXT,
                category       TEXT,
                subcategory    TEXT,
                confidence     REAL,
                low_confidence INTEGER DEFAULT 0,
                status         TEXT DEFAULT 'pending',
                processor_refs TEXT,
                error_message  TEXT,
                updated_at     DATETIME
            );

            CREATE TABLE IF NOT EXISTS watcher_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS notifier_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id    TEXT,
                type       TEXT,
                created_at DATETIME,
                notified   INTEGER DEFAULT 0
            );
        """)


def upsert_file(file_id: str, **kwargs):
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    for k, v in kwargs.items():
        if isinstance(v, (dict, list)):
            kwargs[k] = json.dumps(v, ensure_ascii=False)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT file_id FROM files WHERE file_id = ?", (file_id,)
        ).fetchone()

        if existing:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            conn.execute(
                f"UPDATE files SET {sets} WHERE file_id = ?",
                [*kwargs.values(), file_id],
            )
        else:
            kwargs["file_id"] = file_id
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join("?" * len(kwargs))
            conn.execute(
                f"INSERT INTO files ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )


def get_file(file_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE file_id = ?", (file_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    for field in ("dates", "processor_refs"):
        if result.get(field):
            result[field] = json.loads(result[field])
    return result


def is_processed(file_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM files WHERE file_id = ?", (file_id,)
        ).fetchone()
    return row is not None and row["status"] in ("processed", "unprocessable", "failed")


def get_watcher_state(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM watcher_state WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else None


def set_watcher_state(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watcher_state (key, value) VALUES (?, ?)",
            (key, value),
        )


def add_notifier_queue(file_id: str, type: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO notifier_queue (file_id, type, created_at) VALUES (?, ?, ?)",
            (file_id, type, datetime.utcnow().isoformat()),
        )


def get_pending_notifications(type: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notifier_queue WHERE type = ? AND notified = 0",
            (type,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_notified(ids: list[int]):
    with get_conn() as conn:
        conn.execute(
            f"UPDATE notifier_queue SET notified = 1 WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
