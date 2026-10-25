from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from lanbridge.core.errors import BusyError, PlatformError, Stopped
from lanbridge.core.models import CopyRoot, Progress, TaskSpec, TaskState
from lanbridge.core.store import Store

from . import robocopy
from .planning import Preflight, preflight_failed, roots_for, scan
from .verification import ProgressSampler, verify

logger = logging.getLogger("lanbridge.transfer")


class TransferEngine:
    def __init__(self, store: Store, data_dir: Path, runner: robocopy.ProcessRunner | None = None):
        self.store, self.data_dir = store, data_dir
        self.runner = runner or robocopy.ProcessRunner()
        self.stop_event = threading.Event()
        self._gate = threading.Lock()
        self._cancel = False

    @property
    def active(self) -> bool:
        return self._gate.locked()

    def stop(self, *, cancel: bool = False) -> None:
        self._cancel = cancel
        self.stop_event.set()

    def prepare(self, spec: TaskSpec, notify: Callable[[Progress], None]) -> Preflight:
        if not self._gate.acquire(blocking=False):
            raise BusyError("Another transfer/scan is active.")
        self.stop_event.clear()
        self._cancel = False
        self.store.set_state(spec.id, TaskState.SCANNING)
        last = 0.0

        def counted(files: int, size: int) -> None:
            nonlocal last
            if time.monotonic() - last > 0.4:
                notify(Progress(spec.id, TaskState.SCANNING, total_bytes=size, total_files=files,
                                message="Calculating size, permissions and destination differences…"))
                last = time.monotonic()
        try:
            _, result = scan(self.store, spec, self.data_dir, self.stop_event, counted)
            self.store.set_state(spec.id, TaskState.READY)
            return result
        except Stopped as exc:
            self.store.set_state(spec.id, TaskState.PAUSED, str(exc))
            raise
        except Exception as exc:
            self.store.set_state(spec.id, TaskState.FAILED, str(exc))
            raise
        finally:
            self._gate.release()

    def run(self, spec: TaskSpec, notify: Callable[[Progress], None], *, dry: bool = False,
            failed_only: bool = False, preflight_done: bool = False) -> None:
        if not self._gate.acquire(blocking=False):
            raise BusyError("Only one task may access the transfer destination at a time.")
        self.stop_event.clear()
        self._cancel = False
        started = time.monotonic()
        last_tick = started
        last_bytes = 0
        last_rate = 0.0
        current = ""
        initial = 0
        status = TaskState.RUNNING
        try:
            robocopy.executable()
            if spec.settings.rate_mib and not robocopy.supports_rate_limit():
                raise PlatformError("This Windows Robocopy does not support /IORATE. Set the limit to 0.")
            if not preflight_done and not failed_only:
                self.store.set_state(spec.id, TaskState.SCANNING)
                scan(self.store, spec, self.data_dir, self.stop_event,
                     lambda n, b: notify(Progress(spec.id, TaskState.SCANNING, total_bytes=b, total_files=n)))
            roots = roots_for(spec, self.data_dir)
            if failed_only:
                preflight_failed(self.store, spec, self.stop_event)
            initial = self.store.totals(spec.id)["matched_bytes"]
            last_bytes = initial
            sampler = ProgressSampler(self.store, spec)
            self.store.set_state(spec.id, TaskState.RUNNING)
            log_dir = self.data_dir / "logs" / spec.id
            log_dir.mkdir(parents=True, exist_ok=True)

            def tick(force: bool = False) -> None:
                nonlocal last_tick, last_bytes, last_rate
                now = time.monotonic()
                if not force and now - last_tick < 2:
                    return
                if status == TaskState.RUNNING and not dry:
                    sampler.sample()
                totals = self.store.totals(spec.id)
                elapsed = now - started
                last_rate = max(0.0, (totals["matched_bytes"] - last_bytes) / max(0.01, now - last_tick))
                average = max(0, totals["matched_bytes"] - initial) / max(elapsed, 0.01)
                remaining = max(0, totals["total_bytes"] - totals["matched_bytes"])
                notify(Progress(spec.id, TaskState.STOPPING if self.stop_event.is_set() else status,
                                **totals, elapsed=elapsed, rate=last_rate, average_rate=average,
                                eta=remaining / average if average else None, current_file=current,
                                message="Observed metadata progress; completion throughput is estimated."))
                last_tick, last_bytes = now, totals["matched_bytes"]

            max_code = 0

            def copy_one(root: CopyRoot, index: int) -> None:
                nonlocal current, max_code
                if self.stop_event.is_set():
                    raise Stopped("Stopped before next copy operation.")
                current = str(Path(root.source) / root.file_name) if root.file_name else root.source
                log = log_dir / f"{time.time_ns()}_{index}{'_dry' if dry else ''}.utf16.log"
                session = self.store.begin_session(spec.id, log, dry)

                def set_pid(pid: int) -> None:
                    with self.store.connect() as db:
                        db.execute("UPDATE sessions SET pid=? WHERE id=?", (pid, session))
                try:
                    code = self.runner.run(robocopy.command(root, spec.settings, log, dry=dry),
                                           self.stop_event, tick, set_pid)
                    self.store.end_session(session, code, robocopy.explain_exit(code))
                except Exception:
                    self.store.end_session(session, -1, "Process supervision error")
                    raise
                if self.stop_event.is_set():
                    raise Stopped("Robocopy exited after the stop request. Completed files are retained.")
                max_code = max(max_code, 16 if code < 0 else code)

            if failed_only:
                count = 0
                # A failed file gets a literal-file Robocopy call. No unbounded argv or wildcard retry.
                for batch in self.store.entries(spec.id, status="failed"):
                    for row in batch:
                        src, dst = Path(row["source"]), Path(row["target"])
                        if row["kind"] == "dir":
                            dst.mkdir(parents=True, exist_ok=True)
                            continue
                        copy_one(CopyRoot(str(src.parent), str(dst.parent), src.name), count)
                        count += 1
                if not count:
                    raise PlatformError("No failed files are recorded. Use Resume for a full source comparison.")
            else:
                for index, root in enumerate(roots):
                    copy_one(root, index)
            if dry:
                dry_state = TaskState.READY if max_code < 8 else TaskState.FAILED
                self.store.set_state(spec.id, dry_state, "Dry run completed. No user files were copied.")
                notify(Progress(spec.id, dry_state, **self.store.totals(spec.id), final=True,
                                message=f"Dry run exit {max_code}: {robocopy.explain_exit(max_code)}"))
                return
            status = TaskState.VERIFYING
            self.store.set_state(spec.id, status)

            def verifying(path: str) -> None:
                nonlocal current
                current = path
                tick()
            verified = verify(self.store, spec, roots, self.stop_event, verifying)
            success = verified and 0 <= max_code < 8
            status = TaskState.COMPLETED if success else TaskState.FAILED
            message = ("SHA-256 verification completed." if spec.settings.hash_verify else
                       "Metadata verification completed; file contents were not cryptographically verified.")
            if not success:
                message = f"Verification/copy errors remain. Robocopy exit {max_code}: {robocopy.explain_exit(max_code)}"
            self.store.set_state(spec.id, status, message)
            notify(Progress(spec.id, status, **self.store.totals(spec.id), elapsed=time.monotonic() - started,
                            message=message, final=True))
        except Stopped as exc:
            status = TaskState.CANCELLED if self._cancel else TaskState.PAUSED
            self.store.set_state(spec.id, status, str(exc))
            notify(Progress(spec.id, status, **self.store.totals(spec.id), message=str(exc), final=True))
        except Exception as exc:
            logger.exception("Transfer task %s failed", spec.id)
            self.store.set_state(spec.id, TaskState.FAILED, str(exc))
            notify(Progress(spec.id, TaskState.FAILED, **self.store.totals(spec.id), message=str(exc), final=True))
            raise
        finally:
            self._gate.release()
