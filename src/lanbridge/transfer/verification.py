from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path

from lanbridge.core.errors import Stopped, ValidationError
from lanbridge.core.models import CopyRoot, TaskSpec
from lanbridge.core.paths import safe_path
from lanbridge.core.store import Store

from .planning import metadata_match, walk_root


def sha256(path: Path, stop: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        while data := stream.read(4 * 1024 * 1024):
            if stop.is_set():
                raise Stopped("Hash verification stopped.")
            digest.update(data)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise OSError("File changed while hashing.")
    return digest.hexdigest()


def verify(store: Store, spec: TaskSpec, roots: list[CopyRoot], stop: threading.Event,
           on_tick: Callable[[str], None]) -> bool:
    tolerance = 2_000_000_000 if spec.settings.fat_times else 100
    ok = True
    for batch in store.entries(spec.id):
        updates = []
        for entry in batch:
            if stop.is_set():
                raise Stopped("Verification stopped; resume will verify again.")
            src, dst = Path(entry["source"]), Path(entry["target"])
            error = ""
            digest = None
            try:
                safe_path(str(src), must_exist=True)
                safe_path(str(dst), must_exist=True)
                if entry["kind"] == "dir":
                    if not src.is_dir() or not dst.is_dir():
                        error = "Missing source/destination directory."
                elif not metadata_match(src, dst, entry["size"], entry["mtime"], tolerance):
                    error = "Missing/changed file or size/time mismatch."
                elif spec.settings.hash_verify:
                    digest = sha256(src, stop)
                    if digest != sha256(dst, stop):
                        error = "SHA-256 mismatch."
                    elif not metadata_match(src, dst, entry["size"], entry["mtime"], tolerance):
                        error = "File changed during final verification."
            except (OSError, ValidationError) as exc:
                error = f"Verification read error: {exc}"
            if error:
                ok = False
            updates.append(("failed" if error else "verified", error, digest, spec.id, str(dst)))
        with store.connect() as db:
            db.executemany("UPDATE entries SET status=?,error=?,digest=? WHERE task_id=? AND target=?", updates)
        on_tick(batch[-1]["source"])
    # Detect new source entries; no point-in-time filesystem snapshot is claimed.
    with store.connect() as db:
        for root in roots:
            for src, dst, kind in walk_root(root, stop):
                if kind == "excluded":
                    continue
                existing = db.execute("SELECT 1 FROM entries WHERE task_id=? AND target=?",
                                      (spec.id, str(dst))).fetchone()
                if not existing:
                    ok = False
                    info = src.stat()
                    db.execute("""INSERT INTO entries(task_id,target,source,kind,size,mtime,status,error)
                                  VALUES(?,?,?,?,?,?,'failed','Source added after preflight; resume to include it.')""",
                               (spec.id, str(dst), str(src), kind,
                                info.st_size if kind == "file" else 0, info.st_mtime_ns))
    return ok


class ProgressSampler:
    """Bounded round-robin observations. Not a byte-level network throughput meter."""

    def __init__(self, store: Store, spec: TaskSpec):
        self.store, self.spec = store, spec
        self.cursor = ""

    def sample(self, limit: int = 256) -> None:
        with self.store.connect() as db:
            rows = db.execute("""SELECT * FROM entries WHERE task_id=? AND kind='file'
                                 AND target>? COLLATE NOCASE ORDER BY target COLLATE NOCASE LIMIT ?""",
                              (self.spec.id, self.cursor, limit)).fetchall()
        if not rows:
            self.cursor = ""
            return
        self.cursor = rows[-1]["target"]
        tolerance = 2_000_000_000 if self.spec.settings.fat_times else 100
        updates = []
        for row in rows:
            matched = metadata_match(Path(row["source"]), Path(row["target"]), row["size"], row["mtime"], tolerance)
            status = "matched" if matched else "pending"
            if row['status'] != status:
                updates.append((status, self.spec.id, row["target"]))
        with self.store.connect() as db:
            db.executemany("UPDATE entries SET status=? WHERE task_id=? AND target=?", updates)
