import threading

import pytest

from lanbridge.core.errors import BusyError, ValidationError
from lanbridge.core.models import TaskSpec
from lanbridge.core.store import Store
from lanbridge.network.conflicts import check_address
from lanbridge.services.application import ApplicationService
from lanbridge.transfer.planning import scan
from lanbridge.transfer.verification import verify


def test_thousands_files_streamed_and_exact_counters(tmp_path):
    source, target, data = tmp_path / 'source', tmp_path / 'target', tmp_path / 'data'
    source.mkdir()
    target.mkdir()
    for n in range(3000):
        (source / f'{n:05}.txt').write_bytes(b'x' * 3)
    store = Store(data / 'db')
    spec = TaskSpec((str(source),), str(target), False)
    store.create(spec)
    _, result = scan(store, spec, data, threading.Event())
    assert result.total_files == store.totals(spec.id)['total_files'] == 3000
    assert store.totals(spec.id)['total_bytes'] == 9000
    batches = list(store.entries(spec.id, batch_size=127))
    assert len(batches) == 24 and max(map(len, batches)) <= 127
    with store.connect() as db:
        db.execute("UPDATE entries SET status='failed' WHERE task_id=? AND kind='file'", (spec.id,))
    assert store.totals(spec.id)['failed_files'] == 3000
    with store.connect() as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_changed_or_added_source_never_reports_complete(tmp_path):
    import shutil
    source, target, data = tmp_path / 'source', tmp_path / 'target', tmp_path / 'data'
    source.mkdir()
    target.mkdir()
    (source / 'original').write_bytes(b'original')
    store = Store(data / 'db')
    spec = TaskSpec((str(source),), str(target), False)
    store.create(spec)
    roots, _ = scan(store, spec, data, threading.Event())
    shutil.copy2(source / 'original', target / 'original')
    (source / 'added').write_bytes(b'new data')
    assert not verify(store, spec, roots, threading.Event(), lambda _: None)
    assert store.totals(spec.id)['failed_files'] == 1


def test_unicode_casefold_collision_is_blocked(tmp_path):
    source, other, target, data = [tmp_path / x for x in ('source', 'other', 'target', 'data')]
    for path in (source, other, target):
        path.mkdir()
    (source / 'É.txt').write_text('one')
    (other / 'é.txt').write_text('two')
    store = Store(data / 'db')
    spec = TaskSpec((str(source), str(other)), str(target), False)
    store.create(spec)
    with pytest.raises(ValidationError, match='same destination'):
        scan(store, spec, data, threading.Event())


def test_network_and_share_changes_blocked_by_active_service(tmp_path):
    service = ApplicationService(tmp_path / 'data')
    with service.exclusive(), pytest.raises(BusyError):
        service.local_change('firewall_add', {})


def test_positive_local_ipv4_conflict_blocks_before_uac():
    adapters = [{'index': 2, 'guid': 'eth', 'status': 'Up', 'ipv4': [{'ip': '192.168.50.1', 'prefix': 24}]},
                {'index': 3, 'guid': 'wifi', 'status': 'Up', 'ipv4': [{'ip': '192.168.50.2', 'prefix': 24}]}]
    with pytest.raises(ValidationError, match='another local adapter'):
        check_address({'index': 2, 'guid': 'eth', 'ip': '192.168.50.2'}, adapters)


def test_resume_authenticates_each_unc_share_once_without_password_argv(tmp_path):
    service = ApplicationService(tmp_path / 'data')
    calls = []
    service.credentials.connect = calls.append
    spec = TaskSpec((r'\\PC-B\Share\folder', r'\\PC-B\Share\file.txt'), r'\\PC-C\Target')
    service._connect_task_shares(spec)
    assert calls == [r'\\PC-B\Share', r'\\PC-C\Target']


def test_merged_sources_count_extra_destination_entries_once(tmp_path):
    a, b, target, data = [tmp_path / x for x in ('a', 'b', 'target', 'data')]
    for directory in (a, b, target):
        directory.mkdir()
    (a / 'a.txt').write_text('a')
    (b / 'b.txt').write_text('b')
    (target / 'extra').write_text('keep')
    store = Store(data / 'db')
    spec = TaskSpec((str(a), str(b)), str(target), False)
    store.create(spec)
    _, info = scan(store, spec, data, threading.Event())
    assert info.extras == 1 and len(info.sources) == 2
