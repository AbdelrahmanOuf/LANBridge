from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .models import IN_FLIGHT, TaskSpec, TaskState


class Store:
    """Short-lived per-thread connections, WAL and durable session boundaries.

    Manifest lives in SQLite rather than a list of a million Python objects.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("This database was created by a newer LANBridge Pro version.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, message TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS entries(
                    task_id TEXT NOT NULL REFERENCES tasks(id), target TEXT NOT NULL COLLATE NOCASE,
                    source TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
                    mtime INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT NOT NULL DEFAULT '', digest TEXT, baseline INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(task_id,target));
                CREATE INDEX IF NOT EXISTS entry_status ON entries(task_id,status);
                CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    started REAL NOT NULL, ended REAL, exit_code INTEGER, log TEXT NOT NULL,
                    dry INTEGER NOT NULL DEFAULT 0, pid INTEGER, note TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL,
                    snapshot TEXT NOT NULL, outcome TEXT, state TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cleanup_events(
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), source TEXT NOT NULL,
                    target TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
                PRAGMA user_version=1;
            """)
            # v2: exact O(1) totals. A million-file task must not be aggregated on
            # every GUI tick. Triggers keep counters transactional with checkpoints.
            db.execute("""CREATE TABLE IF NOT EXISTS task_stats(
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                total_bytes INTEGER NOT NULL DEFAULT 0, total_files INTEGER NOT NULL DEFAULT 0,
                matched_bytes INTEGER NOT NULL DEFAULT 0, matched_files INTEGER NOT NULL DEFAULT 0,
                verified_files INTEGER NOT NULL DEFAULT 0, skipped_files INTEGER NOT NULL DEFAULT 0,
                failed_files INTEGER NOT NULL DEFAULT 0)""")
            measures = {
                'total_bytes': "CASE WHEN {r}.kind='file' THEN {r}.size ELSE 0 END",
                'total_files': "CASE WHEN {r}.kind='file' THEN 1 ELSE 0 END",
                'matched_bytes': "CASE WHEN {r}.kind='file' AND {r}.status IN ('matched','verified','moved') THEN {r}.size ELSE 0 END",
                'matched_files': "CASE WHEN {r}.kind='file' AND {r}.status IN ('matched','verified','moved') THEN 1 ELSE 0 END",
                'verified_files': "CASE WHEN {r}.kind='file' AND {r}.status IN ('verified','moved') THEN 1 ELSE 0 END",
                'skipped_files': "CASE WHEN {r}.kind='file' AND {r}.baseline=1 THEN 1 ELSE 0 END",
                'failed_files': "CASE WHEN {r}.status='failed' THEN 1 ELSE 0 END",
            }
            if version < 2:
                columns = ','.join(measures)
                sums = ','.join(f'COALESCE(SUM({expr.format(r="entries")}),0)' for expr in measures.values())
                db.execute(f'INSERT OR REPLACE INTO task_stats(task_id,{columns}) SELECT task_id,{sums} FROM entries GROUP BY task_id')
            for event in ('INSERT', 'DELETE', 'UPDATE'):
                terms = []
                for key, expr in measures.items():
                    new = expr.format(r='NEW') if event != 'DELETE' else '0'
                    old = expr.format(r='OLD') if event != 'INSERT' else '0'
                    terms.append(f'{key}={key}+({new})-({old})')
                record = 'OLD' if event == 'DELETE' else 'NEW'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS entries_stats_{event.lower()}
                    AFTER {event} ON entries BEGIN
                    INSERT OR IGNORE INTO task_stats(task_id) VALUES({record}.task_id);
                    UPDATE task_stats SET {','.join(terms)} WHERE task_id={record}.task_id;
                    END''')
            db.execute('PRAGMA user_version=3')

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        # SQLite's default NOCASE covers ASCII only. Conservative Unicode
        # casefold prevents selected sources from silently colliding on Windows.
        db.create_collation('NOCASE', lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, spec: TaskSpec) -> None:
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", (spec.id, json.dumps(spec.as_dict()),
                       TaskState.DRAFT, now, now, ""))

    def spec(self, task_id: str) -> TaskSpec:
        with self.connect() as db:
            row = db.execute("SELECT spec FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return TaskSpec.from_dict(json.loads(row[0]))

    def set_state(self, task_id: str, state: TaskState, message: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET state=?,message=?,updated=? WHERE id=?",
                       (state, message, time.time(), task_id))

    def tasks(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM tasks ORDER BY updated DESC")]

    def recover(self) -> int:
        with self.connect() as db:
            count = 0
            for state in IN_FLIGHT:
                count += db.execute("UPDATE tasks SET state=?,message=?,updated=? WHERE state=?",
                                    (TaskState.INTERRUPTED, "Session interrupted; recheck and resume.",
                                     time.time(), state)).rowcount
            db.execute("UPDATE sessions SET ended=?,note='Interrupted application session' WHERE ended IS NULL",
                       (time.time(),))
            return count

    def entries(self, task_id: str, *, status: str | None = None, batch_size: int = 512
                ) -> Iterator[list[sqlite3.Row]]:
        # Release read transaction between pages; progress writes never block behind a long iterator.
        cursor_key = ""
        while True:
            with self.connect() as db:
                sql = "SELECT * FROM entries WHERE task_id=? AND target>? COLLATE NOCASE"
                args: list[Any] = [task_id, cursor_key]
                if status:
                    sql += " AND status=?"
                    args.append(status)
                sql += " ORDER BY target COLLATE NOCASE LIMIT ?"
                args.append(batch_size)
                rows = db.execute(sql, args).fetchall()
            if not rows:
                return
            cursor_key = rows[-1]["target"]
            yield rows

    def totals(self, task_id: str) -> dict[str, int]:
        with self.connect() as db:
            keys = ['total_bytes', 'total_files', 'matched_bytes', 'matched_files',
                    'verified_files', 'skipped_files', 'failed_files']
            row = db.execute('SELECT * FROM task_stats WHERE task_id=?', (task_id,)).fetchone()
            return {key: int(row[key]) if row else 0 for key in keys}

    def begin_session(self, task_id: str, log: Path, dry: bool) -> int:
        with self.connect() as db:
            cursor = db.execute("INSERT INTO sessions(task_id,started,log,dry) VALUES(?,?,?,?)",
                                (task_id, time.time(), str(log), int(dry)))
            return int(cursor.lastrowid)

    def end_session(self, session: int, code: int, note: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE sessions SET ended=?,exit_code=?,note=? WHERE id=?",
                       (time.time(), code, note, session))

    def sessions(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM sessions WHERE task_id=? ORDER BY id",
                                               (task_id,))]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def setting(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value)))

    def operation(self, op_id: str, action: str, payload: dict, snapshot: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?)", (op_id, action,
                       json.dumps(payload), json.dumps(snapshot), None, "prepared", time.time()))

    def finish_operation(self, op_id: str, result: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE operations SET outcome=?,state=? WHERE id=?",
                       (json.dumps(result), "applied" if result.get("ok") else "failed", op_id))

    def operations(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM operations ORDER BY created DESC")]
