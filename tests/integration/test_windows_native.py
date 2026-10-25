"""Non-destructive real Windows API/Robocopy checks; no LAN credentials or UAC mutations."""
import os
import threading

import pytest

pytestmark = [pytest.mark.windows, pytest.mark.skipif(os.name != 'nt', reason='Real Windows host required')]


def test_native_temp_copy_and_skip():
    from lanbridge.selftest import run
    assert run()['ok']


def test_locked_source_rejected(tmp_path):
    import win32con
    import win32file
    from lanbridge.core.models import TaskSpec
    from lanbridge.core.store import Store
    from lanbridge.transfer.planning import scan
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    file = source / 'locked.txt'
    file.write_text('locked')
    handle = win32file.CreateFile(str(file), win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                  0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        store = Store(tmp_path / 'data/db')
        spec = TaskSpec((str(source),), str(target), False)
        store.create(spec)
        with pytest.raises(PermissionError):
            scan(store, spec, tmp_path / 'data', threading.Event())
    finally:
        handle.Close()


def test_local_source_move_requires_hash_and_retains_destination(tmp_path):
    from lanbridge.core.models import TaskSpec, TransferSettings
    from lanbridge.core.store import Store
    from lanbridge.transfer.engine import TransferEngine
    from lanbridge.transfer.move import verified_cleanup
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'cleanup.txt').write_text('verified move')
    data = tmp_path / 'data'
    store = Store(data / 'db')
    spec = TaskSpec((str(source),), str(target), False, TransferSettings(hash_verify=True))
    store.create(spec)
    TransferEngine(store, data).run(spec, lambda _: None)
    assert verified_cleanup(store, spec)['removed_source_files'] == 1
    assert not (source / 'cleanup.txt').exists()
    assert (target / 'cleanup.txt').read_text() == 'verified move'
