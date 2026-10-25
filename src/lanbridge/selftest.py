from __future__ import annotations

import os
import importlib
import tempfile
import threading
from pathlib import Path

from lanbridge.core.models import TaskSpec, TransferSettings
from lanbridge.core.store import Store
from lanbridge.transfer.planning import scan
from lanbridge.transfer.robocopy import ProcessRunner, command
from lanbridge.transfer.verification import verify


def run() -> dict:
    if os.name != 'nt':
        raise RuntimeError('A Windows EXE must be self-tested on Windows; this is not a Windows host.')
    from lanbridge.platform.powershell import PowerShell
    from lanbridge.ui.style import icon
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    if icon('app').isNull() or icon('network').isNull():
        raise RuntimeError('Packaged icon resources are missing.')
    # Native API imports exercise PyInstaller's win32 dependency hooks without changing security settings.
    for module in ('win32cred', 'win32job', 'win32pipe', 'win32wnet', 'win32security', 'win32com.shell.shell'):
        importlib.import_module(module)
    ps = PowerShell()
    adapters = ps.call('adapters')
    with tempfile.TemporaryDirectory(prefix='LANBridge-selftest-') as temp:
        root = Path(temp)
        source, destination = root / 'source', root / 'destination'
        source.mkdir()
        destination.mkdir()
        (source / 'مجلد فارغ').mkdir()
        (source / 'Arabic عربي.txt').write_text('LANBridge Pro — بيانات اختبار', encoding='utf-8')
        (source / 'binary.dat').write_bytes(bytes(range(256)) * 4096)
        (destination / 'EXTRA-KEEP.txt').write_text('retained')
        store = Store(root / 'application/state.db')
        spec = TaskSpec((str(source),), str(destination), False, TransferSettings(hash_verify=True))
        store.create(spec)
        roots, info = scan(store, spec, root / 'application', threading.Event())
        log = root / 'application/selftest.utf16.log'
        runner = ProcessRunner()
        code = runner.run(command(roots[0], spec.settings, log), threading.Event(), lambda: None, lambda _: None)
        if not 0 <= code < 8 or not verify(store, spec, roots, threading.Event(), lambda _: None):
            raise RuntimeError(f'Temporary Robocopy/SHA-256 self-test failed (exit {code}).')
        if not (destination / 'EXTRA-KEEP.txt').exists() or not (source / 'binary.dat').exists():
            raise RuntimeError('Nondestructive-copy check failed.')
        # A second actual Robocopy invocation checks skip semantics in the packaged executable.
        code2 = runner.run(command(roots[0], spec.settings, root / 'application/resume.utf16.log'),
                           threading.Event(), lambda: None, lambda _: None)
        if code2 not in (0, 2):
            raise RuntimeError(f'Resume/skip check failed (exit {code2}).')
        result = {'ok': True, 'platform': 'Windows', 'checks': ['Qt/icons', 'pywin32 hooks',
                  'PowerShell adapter read', 'Robocopy temp copy', 'Arabic names', 'empty directory',
                  'SHA-256 verification', 'source retained', 'extra destination retained', 'second-session skip'],
                  'copy_exit': code, 'repeat_exit': code2, 'file_count': info.total_files,
                  'adapter_count': len(adapters) if isinstance(adapters, list) else int(bool(adapters)),
                  'not_tested': ['two-PC LAN', 'UAC mutations', 'domain policy', 'shutdown recovery on hardware']}
    app.processEvents()
    return result
