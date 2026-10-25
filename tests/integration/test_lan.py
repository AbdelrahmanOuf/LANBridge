"""Explicit opt-in Windows/VM LAN fixture, writing only a unique isolated test folder.

Set LANBRIDGE_LAN_SOURCE to a dedicated LOCAL test folder, and
LANBRIDGE_LAN_DEST to an authorized remote UNC folder. Both may be configured
in PowerShell $env:... variables. No passwords are accepted on command lines.
"""
import os
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.lan, pytest.mark.windows,
              pytest.mark.skipif(os.name != 'nt' or not os.environ.get('LANBRIDGE_LAN_SOURCE')
                                 or not os.environ.get('LANBRIDGE_LAN_DEST'),
                                 reason='Explicit two-PC Windows LAN fixtures required')]


@pytest.fixture
def lan_paths():
    source = Path(os.environ['LANBRIDGE_LAN_SOURCE']) / ('LANBridge-test-' + uuid.uuid4().hex)
    target = Path(os.environ['LANBRIDGE_LAN_DEST']) / source.name
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    # Deliberately leave fixtures for inspection; never recursive-delete a remote tree in test teardown.
    return source, target


def test_unicode_many_files_large_empty_and_repeat(lan_paths, tmp_path):
    from lanbridge.core.models import TaskSpec, TaskState, TransferSettings
    from lanbridge.core.store import Store
    from lanbridge.transfer.engine import TransferEngine
    source, target = lan_paths
    (source / 'فارغ').mkdir()
    (source / 'أسماء عربية بمسافات.txt').write_text('مرحبا من الجهاز الأول', encoding='utf-8')
    for index in range(2000):
        (source / f'small_{index:04}.txt').write_text(f'LANBridge file {index}')
    with (source / 'large.bin').open('wb') as stream:
        for _ in range(64):
            stream.write(os.urandom(1024**2))
    (target / 'EXTRA_KEEP.txt').write_text('must be retained')
    data = tmp_path / 'application'
    store = Store(data / 'test.db')
    spec = TaskSpec((str(source),), str(target), False, TransferSettings(hash_verify=True))
    store.create(spec)
    engine = TransferEngine(store, data)
    engine.run(spec, lambda _: None)
    assert store.tasks()[0]['state'] == TaskState.COMPLETED
    assert (target / 'EXTRA_KEEP.txt').exists()
    assert (target / 'فارغ').is_dir()
    assert (source / 'large.bin').exists()
    engine.run(spec, lambda _: None)
    assert store.totals(spec.id)['skipped_files'] == 2002
    assert store.tasks()[0]['state'] == TaskState.COMPLETED


def test_single_file_smb(lan_paths, tmp_path):
    from lanbridge.core.models import TaskSpec
    from lanbridge.core.store import Store
    from lanbridge.transfer.engine import TransferEngine
    source, target = lan_paths
    (source / 'only.txt').write_text('single file')
    (source / 'not-selected.txt').write_text('do not copy')
    data = tmp_path / 'application'
    store = Store(data / 'test.db')
    spec = TaskSpec((str(source / 'only.txt'),), str(target))
    store.create(spec)
    TransferEngine(store, data).run(spec, lambda _: None)
    assert (target / 'only.txt').read_text() == 'single file'
    assert not (target / 'not-selected.txt').exists()
