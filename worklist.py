"""Durable worklist on SQLite.

Unlike the JSON reference in the harness-pattern repo, this version is exercised
under concurrency, so claiming must be atomic. `BEGIN IMMEDIATE` plus a guarded
UPDATE gives an exclusive claim across processes; the JSON version cannot.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    description   TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    owner         TEXT,
    lease_expires REAL NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    version       TEXT NOT NULL DEFAULT 'v0',
    notes         TEXT NOT NULL DEFAULT '',
    result        TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    task_id   TEXT,
    kind      TEXT NOT NULL,
    detail    TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL NOT NULL,
    task_id           TEXT,
    role              TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    seconds           REAL NOT NULL DEFAULT 0
);
"""


@dataclass
class Task:
    task_id: str
    description: str
    state: str
    owner: Optional[str]
    lease_expires: float
    attempts: int
    version: str
    notes: str
    result: Optional[str]


class Worklist:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- events ------------------------------------------------------

    def log(self, kind: str, task_id: Optional[str] = None,
            detail: str = "") -> None:
        self.db.execute("INSERT INTO events (ts, task_id, kind, detail) VALUES (?,?,?,?)",
                        (time.time(), task_id, kind, detail))

    def log_usage(self, task_id: Optional[str], role: str,
                  prompt: int, completion: int, seconds: float) -> None:
        self.db.execute(
            "INSERT INTO usage (ts, task_id, role, prompt_tokens, completion_tokens,"
            " seconds) VALUES (?,?,?,?,?,?)",
            (time.time(), task_id, role, prompt, completion, seconds))

    def events(self, kind: Optional[str] = None) -> list[sqlite3.Row]:
        if kind:
            return self.db.execute(
                "SELECT * FROM events WHERE kind=? ORDER BY id", (kind,)).fetchall()
        return self.db.execute("SELECT * FROM events ORDER BY id").fetchall()

    # ---- task lifecycle ----------------------------------------------

    def add(self, task_id: str, description: str, version: str = "v0") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO tasks (task_id, description, version)"
            " VALUES (?,?,?)", (task_id, description, version))

    def get(self, task_id: str) -> Optional[Task]:
        r = self.db.execute("SELECT * FROM tasks WHERE task_id=?",
                            (task_id,)).fetchone()
        return Task(**dict(r)) if r else None

    def all(self) -> list[Task]:
        return [Task(**dict(r)) for r in
                self.db.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()]

    def claim(self, task_id: str, owner: str, lease_seconds: float = 30) -> bool:
        """Atomic exclusive claim. Returns False if held by a live lease.

        Uses BEGIN IMMEDIATE so two processes cannot both pass the guard.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT owner, lease_expires, state FROM tasks WHERE task_id=?",
                (task_id,)).fetchone()
            if row is None:
                self.db.execute("ROLLBACK")
                return False
            now = time.time()
            live = (row["state"] == "in_progress" and row["lease_expires"] > now
                    and row["owner"] != owner)
            if live:
                self.db.execute("ROLLBACK")
                return False
            self.db.execute(
                "UPDATE tasks SET owner=?, state='in_progress', lease_expires=?,"
                " attempts=attempts+1 WHERE task_id=?",
                (owner, now + lease_seconds, task_id))
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def renew(self, task_id: str, owner: str, lease_seconds: float = 30) -> bool:
        cur = self.db.execute(
            "UPDATE tasks SET lease_expires=? WHERE task_id=? AND owner=?"
            " AND state='in_progress'",
            (time.time() + lease_seconds, task_id, owner))
        return cur.rowcount > 0

    def note(self, task_id: str, text: str) -> None:
        self.db.execute("UPDATE tasks SET notes = notes || ? WHERE task_id=?",
                        ("\n" + text if text else "", task_id))

    def finish(self, task_id: str, owner: str, result: str,
               state: str = "done") -> None:
        self.db.execute(
            "UPDATE tasks SET state=?, owner=NULL, lease_expires=0, result=?"
            " WHERE task_id=?", (state, result, task_id))

    def fail(self, task_id: str, owner: str, reason: str) -> None:
        self.db.execute(
            "UPDATE tasks SET state='failed', owner=NULL, lease_expires=0,"
            " notes = notes || ? WHERE task_id=?", ("\nFAILED: " + reason, task_id))

    def expired(self) -> list[Task]:
        now = time.time()
        return [t for t in self.all()
                if t.state == "in_progress" and t.lease_expires <= now]

    def inflight(self) -> list[Task]:
        return [t for t in self.all() if t.state == "in_progress"]

    def usage_summary(self) -> dict:
        rows = self.db.execute(
            "SELECT role, SUM(prompt_tokens) p, SUM(completion_tokens) c,"
            " COUNT(*) n, SUM(seconds) s FROM usage GROUP BY role").fetchall()
        return {r["role"]: {"prompt": r["p"], "completion": r["c"],
                            "calls": r["n"], "seconds": r["s"]} for r in rows}
