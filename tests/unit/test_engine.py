import shutil

import pytest

from lanbridge.core.models import TaskSpec, TaskState
from lanbridge.core.store import Store
from lanbridge.transfer.engine import TransferEngine


class CopyRunner:
    def __init__(self, code=1, stop=False):
        self.code, self.stop = code, stop
        self.commands = []

    def run(self, args, stop, tick, started):
        self.commands.append(args)
        started(1234)
        source, target = args[1:3]
        if '/L' not in args:
            shutil.copytree(source, target, dirs_exist_ok=True)
        if self.stop:
            stop.set()
        tick()
        return self.code

    def force_stop(self):
        self.stop = True


@pytest.fixture
def engine(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'file.txt').write_text('complete copy')
    data = tmp_path / 'data'
    store = Store(data / 'db')
    spec = TaskSpec((str(source),), str(target), False)
    store.create(spec)
    from lanbridge.transfer import robocopy
    monkeypatch.setattr(robocopy, 'executable', lambda: 'mock-robocopy')
    return store, spec, data, source, target


@pytest.mark.parametrize('code', [0, 1, 2, 3, 4, 5, 6, 7, 8, 16, -1])
def test_real_metadata_verification_and_exit_result(engine, code):
    store, spec, data, source, target = engine
    runner = CopyRunner(code=code)
    events = []
    TransferEngine(store, data, runner).run(spec, events.append)
    assert events[-1].state == (TaskState.COMPLETED if 0 <= code < 8 else TaskState.FAILED)
    assert (source / 'file.txt').exists()
    assert (target / 'file.txt').read_text() == 'complete copy'


def test_stop_keeps_files_and_restart_recovers(engine):
    store, spec, data, source, target = engine
    TransferEngine(store, data, CopyRunner(stop=True)).run(spec, lambda _: None)
    assert store.tasks()[0]['state'] == TaskState.PAUSED
    assert (target / 'file.txt').exists() and (source / 'file.txt').exists()
    progress = []
    TransferEngine(store, data, CopyRunner(0)).run(spec, progress.append)
    assert progress[-1].state == TaskState.COMPLETED
    assert progress[-1].skipped_files == 1


def test_dry_run_preserves_destination(engine):
    store, spec, data, source, target = engine
    runner = CopyRunner(0)
    TransferEngine(store, data, runner).run(spec, lambda _: None, dry=True)
    assert not (target / 'file.txt').exists()
    assert '/L' in runner.commands[0]
    assert store.tasks()[0]['state'] == TaskState.READY


def test_insufficient_disk_blocks_before_process(engine, monkeypatch):
    store, spec, data, source, target = engine
    usage = shutil.disk_usage(target)
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: usage._replace(free=0))
    runner = CopyRunner()
    with pytest.raises(Exception, match='Insufficient'):
        TransferEngine(store, data, runner).run(spec, lambda _: None)
    assert runner.commands == []


def test_missing_source_blocks(engine):
    store, spec, data, source, target = engine
    shutil.rmtree(source)
    runner = CopyRunner()
    with pytest.raises(Exception, match='does not exist'):
        TransferEngine(store, data, runner).run(spec, lambda _: None)
    assert runner.commands == []
