from __future__ import annotations

import ctypes
import hashlib
import os
import time

from lanbridge.core.errors import PlatformError, ValidationError
from lanbridge.core.models import TaskSpec, TaskState
from lanbridge.core.paths import safe_path
from lanbridge.core.store import Store


def verified_cleanup(store: Store, spec: TaskSpec) -> dict:
    """Separate, explicitly confirmed local-file cleanup; never Robocopy /MOVE.

    Both file handles deny new writers/deleters throughout SHA-256 comparison.
    Source deletion uses that same open handle, avoiding a hash-then-unlink race.
    Empty source directories are deliberately retained. No automatic recovery
    ever repeats deletion, and a failure leaves the remaining source files alone.
    """
    if os.name != 'nt':
        raise PlatformError('Verified move cleanup requires Windows file handles.')
    state = next(x['state'] for x in store.tasks() if x['id'] == spec.id)
    if state != TaskState.COMPLETED or not spec.settings.hash_verify:
        raise ValidationError('Complete a copy task with SHA-256 verification before source cleanup.')
    if any(x.startswith('\\\\') for x in spec.sources):
        raise ValidationError('Only the computer owning the local source files may perform source cleanup.')
    import win32con
    import win32file
    from ctypes import wintypes

    class Disposition(ctypes.Structure):
        # FILE_DISPOSITION_INFO uses a one-byte BOOLEAN, not Win32's four-byte BOOL.
        _fields_ = [('DeleteFile', ctypes.c_ubyte)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    setter = kernel.SetFileInformationByHandle
    setter.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    setter.restype = wintypes.BOOL
    removed = 0

    def digest(handle) -> str:
        result = hashlib.sha256()
        while True:
            _, data = win32file.ReadFile(handle, 4 * 1024 * 1024)
            if not data:
                return result.hexdigest()
            result.update(data)

    for batch in store.entries(spec.id):
        for entry in batch:
            if entry['kind'] != 'file' or entry['status'] == 'moved':
                continue
            if entry['status'] != 'verified' or not entry['digest']:
                raise ValidationError('Every source file must have a recorded successful SHA-256 verification.')
            src = safe_path(entry['source'], must_exist=True)
            dst = safe_path(entry['target'], must_exist=True)
            source_handle = win32file.CreateFile(str(src), win32con.GENERIC_READ | win32con.DELETE,
                win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING,
                win32file.FILE_FLAG_OPEN_REPARSE_POINT | win32file.FILE_FLAG_SEQUENTIAL_SCAN, None)
            try:
                destination_handle = win32file.CreateFile(str(dst), win32con.GENERIC_READ,
                    win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING,
                    win32file.FILE_FLAG_OPEN_REPARSE_POINT | win32file.FILE_FLAG_SEQUENTIAL_SCAN, None)
                try:
                    for handle in (source_handle, destination_handle):
                        if win32file.GetFileInformationByHandle(handle)[0] & 0x400:
                            raise ValidationError('A reparse point replaced a verified file.')
                    info = src.stat()
                    if info.st_size != entry['size'] or info.st_mtime_ns != entry['mtime']:
                        raise ValidationError(f'Source changed; cleanup stopped: {src}')
                    if digest(source_handle) != entry['digest'] or digest(destination_handle) != entry['digest']:
                        raise ValidationError(f'Fresh SHA-256 check failed; cleanup stopped: {src}')
                    with store.connect() as db:
                        event = db.execute('''INSERT INTO cleanup_events(task_id,source,target,digest,state,created)
                                              VALUES(?,?,?,?,'prepared',?)''',
                                           (spec.id, str(src), str(dst), entry['digest'], time.time())).lastrowid
                    if not setter(int(source_handle), 4, ctypes.byref(Disposition(True)), ctypes.sizeof(Disposition)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    # Keep destination locked until the deleting source handle closes.
                    source_handle.Close()
                    source_handle = None
                finally:
                    destination_handle.Close()
            finally:
                if source_handle is not None:
                    source_handle.Close()
            with store.connect() as db:
                db.execute("UPDATE cleanup_events SET state='deleted' WHERE id=?", (event,))
                db.execute("UPDATE entries SET status='moved' WHERE task_id=? AND target=?", (spec.id, str(dst)))
            removed += 1
    store.set_state(spec.id, TaskState.COMPLETED,
                    f'Explicit verified cleanup removed {removed} local source files. Source directories retained.')
    return {'removed_source_files': removed, 'source_directories': 'retained',
            'note': 'Destination copies remain. Cleanup is never run automatically on resume.'}
