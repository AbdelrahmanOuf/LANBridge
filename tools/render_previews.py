"""Render the real Qt UI, without advertising or querying the host's network."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from lanbridge.services.application import ApplicationService
from lanbridge.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    target = ROOT / 'docs/previews'
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='LANBridge-preview-') as temp:
        service = ApplicationService(Path(temp) / 'state')
        window = MainWindow(service, refresh_on_start=False)
        window.resize(1366, 960)
        window.show()

        def dashboard() -> None:
            window.grab().save(str(target / 'dashboard-ar-dark.png'))
            window.navigate(4)
            window.transfer_inputs.hide()
            QTimer.singleShot(250, transfer)

        def transfer() -> None:
            window.grab().save(str(target / 'transfer-ar-dark.png'))
            window.navigate(1)
            QTimer.singleShot(250, network)

        def network() -> None:
            window.grab().save(str(target / 'network-ar-dark.png'))
            window.close()
            # Recreate in English/light using persisted preferences, as an actual user restart.
            service.store.setting('language', 'en')
            service.store.setting('theme', 'light')
            english = MainWindow(service, refresh_on_start=False)
            english.resize(1366, 960)
            english.show()
            windows.append(english)
            QTimer.singleShot(250, finish)

        def finish() -> None:
            windows[-1].grab().save(str(target / 'dashboard-en-light.png'))
            windows[-1].close()
            app.quit()

        windows = [window]
        app.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(300, dashboard)
        app.exec()


if __name__ == '__main__':
    main()
