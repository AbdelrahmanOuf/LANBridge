import dataclasses
import json
import threading

import pytest

from lanbridge.core.errors import ValidationError
from lanbridge.core.models import CopyRoot, TaskSpec, TaskState, TransferSettings
from lanbridge.core.paths import safe_path, validate_unc
from lanbridge.core.store import Store
from lanbridge.network.discovery import decode
from lanbridge.network.validation import same_subnet, static_config
from lanbridge.security.credentials import Credentials, classify_error
from lanbridge.transfer.planning import scan
from lanbridge.transfer.robocopy import command, explain_exit
from lanbridge.transfer.verification import verify


@pytest.fixture
def task(tmp_path):
    data = tmp_path / 'appdata'
    store = Store(data / 'test.db')
    source = tmp_path / 'source'
    source.mkdir()
    destination = tmp_path / 'target'
    destination.mkdir()
    spec = TaskSpec((str(source),), str(destination), False)
    store.create(spec)
    return store, spec, data, source, destination


def test_manifest_unicode_empty_and_extra(task):
    store, spec, data, source, destination = task
    (source / 'فارغ').mkdir()
    (source / 'ملف عربي space.txt').write_text('Hello عالم', encoding='utf-8')
    (destination / 'extra.txt').write_text('keep me')
    roots, info = scan(store, spec, data, threading.Event())
    assert info.total_files == 1 and info.extras == 1
    assert store.totals(spec.id)['total_bytes'] == len('Hello عالم'.encode())
    assert roots[0].destination == str(destination)
    assert not verify(store, spec, roots, threading.Event(), lambda _: None)
    assert (destination / 'extra.txt').read_text() == 'keep me'


def test_copy_verified_and_tamper(task):
    import shutil
    store, spec, data, source, destination = task
    (source / 'a.txt').write_bytes(b'hello')
    spec = dataclasses.replace(spec, settings=TransferSettings(hash_verify=True))
    roots, _ = scan(store, spec, data, threading.Event())
    shutil.copy2(source / 'a.txt', destination / 'a.txt')
    assert verify(store, spec, roots, threading.Event(), lambda _: None)
    assert store.totals(spec.id)['verified_files'] == 1
    (destination / 'a.txt').write_bytes(b'other')
    import os
    os.utime(destination / 'a.txt', ns=((source / 'a.txt').stat().st_atime_ns,
                                      (source / 'a.txt').stat().st_mtime_ns))
    assert not verify(store, spec, roots, threading.Event(), lambda _: None)


def test_resume_comparison_and_crash_recovery(task):
    import shutil
    store, spec, data, source, destination = task
    (source / 'done').write_bytes(b'completed')
    (source / 'pending').write_bytes(b'pending')
    shutil.copy2(source / 'done', destination / 'done')
    scan(store, spec, data, threading.Event())
    assert store.totals(spec.id)['skipped_files'] == 1
    store.set_state(spec.id, TaskState.RUNNING)
    recovered = Store(store.path)
    assert recovered.recover() == 1
    assert recovered.spec(spec.id) == spec
    assert recovered.tasks()[0]['state'] == TaskState.INTERRUPTED
    assert (destination / 'done').read_bytes() == b'completed'


@pytest.mark.parametrize('threads', [0, 129, -1, True, '8'])
def test_threads_validation(threads):
    with pytest.raises(ValidationError):
        TransferSettings(threads=threads)


def test_args_are_literal_and_nondestructive(task, tmp_path):
    _, _, _, source, destination = task
    name = "عربي & $(whoami); 'test.txt"
    args = command(CopyRoot(str(source), str(destination), name), TransferSettings(),
                   tmp_path / 'logs/copy.log', binary='robocopy.exe')
    assert args[3] == name
    assert '/MT:8' in args and '/Z' in args
    assert not {'/MIR', '/PURGE', '/MOVE', '/MOV', '/B', '/ZB'} & set(args)
    with pytest.raises(ValidationError):
        command(CopyRoot(str(source), str(destination), '*.txt'), TransferSettings(),
                tmp_path / 'log', binary='robocopy')


@pytest.mark.parametrize('code', range(32))
def test_exit_codes(code):
    text = explain_exit(code)
    assert bool('copy failures' in text) == bool(code & 8)
    assert bool('fatal error' in text) == bool(code & 16)


def test_overlap_rejected(task):
    store, spec, data, source, _ = task
    spec = dataclasses.replace(spec, destination=str(source / 'recursive'))
    with pytest.raises(ValidationError, match='overlap'):
        scan(store, spec, data, threading.Event())


def test_symlink_and_collision(task):
    store, spec, data, source, destination = task
    (source / 'normal').write_text('ok')
    try:
        (source / 'loop').symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip('Host cannot create a test symlink')
    _, result = scan(store, spec, data, threading.Event())
    assert result.excluded == 1
    with pytest.raises(ValidationError):
        safe_path(str(source / 'loop'))
    (destination / 'loop').symlink_to(source, target_is_directory=True)
    with pytest.raises(ValidationError, match='reparse'):
        scan(store, spec, data, threading.Event())


def test_single_file_and_multi_collision(task, tmp_path):
    store, spec, data, source, destination = task
    f = source / 'one.txt'
    f.write_text('one')
    single = dataclasses.replace(spec, sources=(str(f),))
    roots, info = scan(store, single, data, threading.Event())
    assert roots[0].file_name == 'one.txt' and info.total_files == 1
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'one.txt').write_text('two')
    multi = dataclasses.replace(spec, sources=(str(source), str(other)))
    with pytest.raises(ValidationError, match='same destination'):
        scan(store, multi, data, threading.Event())


def test_subnets_and_config():
    p = {'index': 4, 'guid': 'e634c111-caaa-4320-a8df-a16412c92942', 'mode': 'static',
         'ip': '192.168.50.1', 'prefix': '255.255.255.0', 'dns': [], 'gateway': ''}
    assert static_config(p)['prefix'] == 24
    assert same_subnet('192.168.50.1', 24, '192.168.50.2')
    assert not same_subnet('192.168.50.1', 24, '192.168.51.2')
    for patch in ({'ip': '192.168.50.0'}, {'ip': '127.0.0.1'}, {'gateway': '10.1.1.1'},
                  {'prefix': '255.0.255.0'}, {'index': 0}):
        with pytest.raises((ValidationError, ValueError)):
            static_config(p | patch)


def test_unc_and_discovery_untrusted():
    assert validate_unc(r'\\PC-B\LANBridge_abcd\مجلد')[0] == 'PC-B'
    for path in (r'\\PC-B\share\..\secret', r'\\PC-B\share;\x\..', 'PC-B/share'):
        with pytest.raises(ValidationError):
            validate_unc(path)
    data = {'app': 'LANBridgePro', 'v': 1, 'nonce': 'a' * 32, 'kind': 'probe'}
    assert decode(json.dumps(data).encode()) == data
    with pytest.raises(ValidationError):
        decode(b'x' * 769)


def test_no_plaintext_credential_repr():
    secret = Credentials('DOMAIN\\user', 'secret not logged')
    assert 'secret not logged' not in repr(secret)
    assert '1219' not in classify_error(1219)  # actionable account conflict explanation
