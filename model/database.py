"""SQLite 저장소 (db/medicine.db)

센서 스레드와 웹 요청이 동시에 쓰므로 연결 하나를 잠금(lock)으로 보호합니다.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY,
    medicine TEXT NOT NULL,
    start_date TEXT NOT NULL,
    days INTEGER NOT NULL,
    window_min INTEGER NOT NULL,
    slots TEXT NOT NULL,          -- JSON 배열
    prn TEXT,                     -- JSON 또는 NULL
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (          -- 복약 기록 (시간대당 1건)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    count INTEGER NOT NULL,
    water INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'station',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (           -- 뚜껑 열림, 중복 시도 등 판정 기록
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    slot TEXT,
    value REAL
);
CREATE TABLE IF NOT EXISTS alerts (           -- 보호자 알림
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    read_at TEXT,
    dedupe_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS activity (         -- 약통 앞 움직임
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    date TEXT NOT NULL,
    hour INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (               -- 설정, 보정값, 남은 약
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date, type);
CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(date);
"""


def iso(dt: datetime) -> str:
    """datetime -> '2026-11-23T08:12:00'"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        """INSERT/UPDATE/DELETE. (새 행 id, 바뀐 행 수)를 돌려줌"""
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid, cur.rowcount

    def get_kv(self, key, default=None):
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_kv(self, key, value):
        self.execute("INSERT INTO kv(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))
