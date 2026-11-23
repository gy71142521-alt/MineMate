"""Event stores.

`EventStore` is a deliberately small seam: durable session records plus ordered
events. SQLite is the production backend; the in-memory backend exists so tests
and future embeddings can exercise the same contract without touching disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol, Sequence

from .types import (
    MEMORY_FORMAT_VERSION,
    EventType,
    MemoryCorruptError,
    MemoryStoreError,
    SessionEvent,
    SessionRecord,
)


class EventStore(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def create_session(self, player: str, *, now: float | None = None) -> SessionRecord: ...
    def latest_session(self, player: str) -> SessionRecord | None: ...
    def load_events(self, session_id: str) -> list[SessionEvent]: ...
    def append(self, session_id: str, events: Sequence[SessionEvent], *, now: float | None = None) -> SessionRecord: ...
    def delete_session(self, session_id: str) -> None: ...
    def list_sessions(self, *, limit: int = 100) -> list[SessionRecord]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_sessions (
    id TEXT PRIMARY KEY,
    player TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT -1
);
CREATE INDEX IF NOT EXISTS memory_sessions_player ON memory_sessions(player, updated_at DESC);
CREATE TABLE IF NOT EXISTS memory_events (
    session_id TEXT NOT NULL REFERENCES memory_sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    time REAL NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""


class SQLiteEventStore:
    """Single-process SQLite store with WAL and a process-local write lock."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn: sqlite3.Connection | None = None
            try:
                conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None,
                                       check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(_SCHEMA)
                row = conn.execute("SELECT value FROM memory_meta WHERE key='format_version'").fetchone()
                if row is None:
                    conn.execute("INSERT INTO memory_meta(key, value) VALUES('format_version', ?)",
                                 (str(MEMORY_FORMAT_VERSION),))
                elif int(row["value"]) != MEMORY_FORMAT_VERSION:
                    raise MemoryStoreError(
                        f"记忆数据库格式版本不兼容：{row['value']} != {MEMORY_FORMAT_VERSION}。")
                self._conn = conn
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            except (sqlite3.Error, OSError, MemoryStoreError, ValueError) as error:
                if conn is not None:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
                if isinstance(error, MemoryStoreError):
                    raise
                raise MemoryStoreError("无法打开记忆数据库。") from error

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def create_session(self, player: str, *, now: float | None = None) -> SessionRecord:
        created = time.time() if now is None else float(now)
        record = SessionRecord(id=uuid.uuid4().hex, player=player, created_at=created,
                               updated_at=created, last_seq=-1)
        with self._lock:
            try:
                with self._connection() as conn:
                    conn.execute(
                        "INSERT INTO memory_sessions(id, player, created_at, updated_at, last_seq) VALUES(?,?,?,?,?)",
                        (record.id, record.player, record.created_at, record.updated_at, record.last_seq),
                    )
            except sqlite3.Error as error:
                raise MemoryStoreError("无法创建记忆会话。") from error
        return record

    def latest_session(self, player: str) -> SessionRecord | None:
        with self._lock:
            try:
                row = self._connection().execute(
                    "SELECT id, player, created_at, updated_at, last_seq FROM memory_sessions "
                    "WHERE lower(player)=lower(?) ORDER BY updated_at DESC LIMIT 1",
                    (player,),
                ).fetchone()
            except sqlite3.Error as error:
                raise MemoryStoreError("无法读取记忆会话。") from error
        return _record(row) if row else None

    def load_events(self, session_id: str) -> list[SessionEvent]:
        with self._lock:
            try:
                rows = self._connection().execute(
                    "SELECT seq, time, type, data FROM memory_events WHERE session_id=? ORDER BY seq",
                    (session_id,),
                ).fetchall()
            except sqlite3.Error as error:
                raise MemoryStoreError("无法读取记忆事件。") from error
        events: list[SessionEvent] = []
        for row in rows:
            try:
                events.append(SessionEvent.model_validate({
                    "seq": row["seq"], "time": row["time"], "type": EventType(row["type"]),
                    "data": json.loads(row["data"]),
                }))
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise MemoryCorruptError(f"记忆事件 {row['seq']} 无法解析。") from error
        return events

    def append(self, session_id: str, events: Sequence[SessionEvent], *, now: float | None = None) -> SessionRecord:
        if not events:
            record = self._require_record(session_id)
            return record
        updated = time.time() if now is None else float(now)
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT last_seq FROM memory_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if row is None:
                    raise MemoryStoreError("记忆会话不存在。")
                expected = int(row["last_seq"]) + 1
                if any(event.seq != expected + offset for offset, event in enumerate(events)):
                    raise MemoryStoreError("记忆事件序号不连续，拒绝写入。")
                conn.executemany(
                    "INSERT INTO memory_events(session_id, seq, time, type, data) VALUES(?,?,?,?,?)",
                    [(session_id, event.seq, event.time, str(event.type),
                      json.dumps(event.data, ensure_ascii=False, allow_nan=False)) for event in events],
                )
                conn.execute(
                    "UPDATE memory_sessions SET updated_at=?, last_seq=? WHERE id=?",
                    (updated, events[-1].seq, session_id),
                )
                conn.execute("COMMIT")
            except Exception as error:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if isinstance(error, MemoryStoreError):
                    raise
                raise MemoryStoreError("无法写入记忆事件。") from error
        return self._require_record(session_id).model_copy(update={"updated_at": updated, "last_seq": events[-1].seq})

    def _require_record(self, session_id: str) -> SessionRecord:
        with self._lock:
            row = self._connection().execute(
                "SELECT id, player, created_at, updated_at, last_seq FROM memory_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise MemoryStoreError("记忆会话不存在。")
        return _record(row)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            try:
                with self._connection() as conn:
                    conn.execute("DELETE FROM memory_sessions WHERE id=?", (session_id,))
            except sqlite3.Error as error:
                raise MemoryStoreError("无法删除记忆会话。") from error

    def list_sessions(self, *, limit: int = 100) -> list[SessionRecord]:
        with self._lock:
            try:
                rows = self._connection().execute(
                    "SELECT id, player, created_at, updated_at, last_seq FROM memory_sessions "
                    "ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 1000)),),
                ).fetchall()
            except sqlite3.Error as error:
                raise MemoryStoreError("无法列出记忆会话。") from error
        return [_record(row) for row in rows]


class InMemoryEventStore:
    """Contract-compatible store used by tests and lightweight embeddings."""

    def __init__(self):
        self._lock = threading.RLock()
        self._records: dict[str, SessionRecord] = {}
        self._events: dict[str, list[SessionEvent]] = {}
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def create_session(self, player: str, *, now: float | None = None) -> SessionRecord:
        created = time.time() if now is None else float(now)
        record = SessionRecord(id=uuid.uuid4().hex, player=player, created_at=created,
                               updated_at=created, last_seq=-1)
        with self._lock:
            self._records[record.id] = record
            self._events[record.id] = []
        return record

    def latest_session(self, player: str) -> SessionRecord | None:
        with self._lock:
            matches = [record for record in self._records.values() if record.player.lower() == player.lower()]
        return max(matches, key=lambda record: record.updated_at) if matches else None

    def load_events(self, session_id: str) -> list[SessionEvent]:
        with self._lock:
            if session_id not in self._records:
                raise MemoryStoreError("记忆会话不存在。")
            return list(self._events[session_id])

    def append(self, session_id: str, events: Sequence[SessionEvent], *, now: float | None = None) -> SessionRecord:
        updated = time.time() if now is None else float(now)
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                raise MemoryStoreError("记忆会话不存在。")
            existing = self._events[session_id]
            expected = (existing[-1].seq + 1) if existing else 0
            if any(event.seq != expected + offset for offset, event in enumerate(events)):
                raise MemoryStoreError("记忆事件序号不连续，拒绝写入。")
            existing.extend(events)
            record = record.model_copy(update={"updated_at": updated,
                                               "last_seq": events[-1].seq if events else record.last_seq})
            self._records[session_id] = record
            return record

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._records.pop(session_id, None)
            self._events.pop(session_id, None)

    def list_sessions(self, *, limit: int = 100) -> list[SessionRecord]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda record: record.updated_at, reverse=True)
        return records[:max(1, min(limit, 1000))]


def _record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(id=row["id"], player=row["player"], created_at=row["created_at"],
                         updated_at=row["updated_at"], last_seq=row["last_seq"])
