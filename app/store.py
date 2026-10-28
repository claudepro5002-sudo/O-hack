"""Хранилище на SQLite: сессии, профили (память), бронирования, события метрик."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .config import env

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def db_path() -> str:
    return env("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "agent.db"))


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        p = db_path()
        if p != ":memory:":
            Path(p).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(p, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user_id TEXT, state TEXT, created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, data TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, data TEXT, status TEXT,
                                             created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id TEXT, type TEXT,
                                           ts REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS places (key TEXT PRIMARY KEY, data TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS feeds (session_id TEXT PRIMARY KEY, data TEXT, updated REAL);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
        """)
    return _conn


def reset_for_tests(path: str = ":memory:") -> None:
    global _conn
    _conn = None
    os.environ["DB_PATH"] = path


def save_session(state) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO sessions(id, user_id, state, created, updated) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated=excluded.updated, user_id=excluded.user_id",
                  (state.id, state.user_id, state.model_dump_json(), state.started_at, time.time()))
        c.commit()


def load_session(session_id: str):
    from .state import SessionState
    with _lock:
        row = conn().execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    return SessionState.model_validate_json(row["state"])


def user_sessions(user_id: str) -> list:
    """Чаты пользователя, свежие сверху."""
    from .state import SessionState
    with _lock:
        rows = conn().execute("SELECT state, updated FROM sessions WHERE user_id=? ORDER BY updated DESC LIMIT 100",
                              (user_id,)).fetchall()
    return [(SessionState.model_validate_json(r["state"]), r["updated"]) for r in rows]


def delete_session(session_id: str) -> None:
    """Удаляем чат и его ленту; брони остаются в «Моих поездках»."""
    with _lock:
        c = conn()
        c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        c.execute("DELETE FROM feeds WHERE session_id=?", (session_id,))
        c.commit()


FEED_LIMIT = 60  # сколько последних сообщений чата храним для показа


def get_feed(session_id: str) -> list[dict]:
    with _lock:
        row = conn().execute("SELECT data FROM feeds WHERE session_id=?", (session_id,)).fetchone()
    return json.loads(row["data"]) if row else []


def save_feed(session_id: str, feed: list[dict]) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO feeds(session_id, data, updated) VALUES(?,?,?) "
                  "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                  (session_id, json.dumps(feed[-FEED_LIMIT:], ensure_ascii=False), time.time()))
        c.commit()


def all_sessions() -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT state FROM sessions").fetchall()
    return [json.loads(r["state"]) for r in rows]


def get_profile(user_id: str) -> dict:
    with _lock:
        row = conn().execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row["data"]) if row else {}


def save_profile(user_id: str, data: dict) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO profiles(user_id, data, updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                  "data=excluded.data, updated=excluded.updated", (user_id, json.dumps(data, ensure_ascii=False), time.time()))
        c.commit()


def delete_profile(user_id: str) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM profiles WHERE user_id=?", (user_id,))
        c.commit()


def save_booking(b: dict) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO bookings(id, user_id, session_id, data, status, created, updated) VALUES(?,?,?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET data=excluded.data, status=excluded.status, updated=excluded.updated",
                  (b["id"], b["user_id"], b["session_id"], json.dumps(b, ensure_ascii=False), b["status"],
                   b.get("created", time.time()), time.time()))
        c.commit()


def get_booking(booking_id: str) -> dict | None:
    with _lock:
        row = conn().execute("SELECT data FROM bookings WHERE id=?", (booking_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def user_bookings(user_id: str) -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT data FROM bookings WHERE user_id=? ORDER BY created DESC", (user_id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def log_event(session_id: str, user_id: str, type_: str, data: dict | None = None) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO events(session_id, user_id, type, ts, data) VALUES(?,?,?,?,?)",
                  (session_id, user_id, type_, time.time(), json.dumps(data or {}, ensure_ascii=False)))
        c.commit()


def events(type_: str | None = None) -> list[dict]:
    with _lock:
        if type_:
            rows = conn().execute("SELECT * FROM events WHERE type=?", (type_,)).fetchall()
        else:
            rows = conn().execute("SELECT * FROM events").fetchall()
    return [dict(r) for r in rows]


def get_place(key: str) -> dict | None:
    with _lock:
        row = conn().execute("SELECT data FROM places WHERE key=?", (key,)).fetchone()
    return json.loads(row["data"]) if row else None


def save_place(key: str, data: dict) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO places(key, data, updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "data=excluded.data, updated=excluded.updated", (key, json.dumps(data, ensure_ascii=False), time.time()))
        c.commit()
