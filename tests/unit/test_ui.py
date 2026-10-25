import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from lanbridge.services.application import ApplicationService
from lanbridge.ui.main_window import MainWindow
from lanbridge.ui.workers import Worker


def test_all_pages_connected_and_render(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = ApplicationService(tmp_path / 'data')
    window = MainWindow(service, refresh_on_start=False)
    window.show()
    for page in range(8):
        window.navigate(page)
        app.processEvents()
        assert window.stack.currentIndex() == page
        assert not window.grab().isNull()
    assert window.threads.minimum() == 1 and window.threads.maximum() == 128
    assert window.sources.layoutDirection().name == 'LeftToRight'
    window.close()
    app.processEvents()


def test_worker_delivers_result_on_gui_thread():
    app = QApplication.instance() or QApplication([])
    main_thread = threading.get_ident()
    observed = []
    worker = Worker(lambda notify: threading.get_ident())
    worker.signals.result.connect(lambda result: observed.append((threading.get_ident(), result)))
    pool = QThreadPool()
    pool.start(worker)
    until = time.monotonic() + 5
    while not observed and time.monotonic() < until:
        app.processEvents()
        time.sleep(.01)
    assert observed[0][0] == main_thread
    assert observed[0][1] != main_thread
    pool.waitForDone()


def test_start_button_workflow_reaches_verified_completion(tmp_path, monkeypatch):
    import shutil
    from lanbridge.transfer import robocopy
    from lanbridge.core.models import TaskState
    app = QApplication.instance() or QApplication([])
    service = ApplicationService(tmp_path / 'data')
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.mkdir()
    destination.mkdir()
    (source / 'ملف.txt').write_text('real metadata verification', encoding='utf-8')
    class Runner:
        def run(self, args, stop, tick, started):
            started(100)
            shutil.copytree(args[1], args[2], dirs_exist_ok=True)
            return 1
    service.engine.runner = Runner()
    monkeypatch.setattr(robocopy, 'executable', lambda: 'test-adapter')
    window = MainWindow(service, refresh_on_start=False)
    errors = []
    window.error = lambda error: errors.append(str(error))
    window.confirm = lambda message: True
    window.add_paths([str(source)])
    window.destination.setText(str(destination))
    window.copy_mode.setCurrentIndex(1)
    window.start_transfer()
    until = time.monotonic() + 8
    while window._critical_jobs and time.monotonic() < until:
        app.processEvents()
        time.sleep(.01)
    assert not errors and not window._critical_jobs
    assert service.store.tasks()[0]['state'] == TaskState.COMPLETED
    assert window.metrics['Verified files'].value.text() == '1'
    assert (source / 'ملف.txt').exists() and (destination / 'ملف.txt').exists()
    window.close()
    app.processEvents()
