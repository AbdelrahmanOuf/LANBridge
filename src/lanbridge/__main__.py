from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog='LANBridgePro')
    parser.add_argument('--admin-worker', nargs=3, metavar=('PIPE', 'NONCE', 'PARENT_PID'), help=argparse.SUPPRESS)
    parser.add_argument('--signal-stop', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--self-test', action='store_true', help='Read-only API checks and isolated temporary copy test')
    parser.add_argument('--self-test-report', type=Path)
    args = parser.parse_args()
    if args.admin_worker:
        from lanbridge.security.admin import worker
        pipe, nonce, parent = args.admin_worker
        return worker(pipe, nonce, int(parent))
    if args.signal_stop is not None:
        from lanbridge.transfer.robocopy import signal_stop
        return signal_stop(args.signal_stop)
    if args.self_test:
        from lanbridge.selftest import run
        try:
            result = run()
            code = 0
        except Exception as exc:
            result, code = {'ok': False, 'error': str(exc)}, 1
        if args.self_test_report:
            args.self_test_report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if sys.stdout:
            print(json.dumps(result, ensure_ascii=True))
        return code
    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from lanbridge.core.logging_config import configure_logging
    from lanbridge.core.paths import app_data
    from lanbridge.services.application import ApplicationService
    from lanbridge.ui.main_window import MainWindow

    application = QApplication(sys.argv[:1])
    application.setApplicationName('LANBridge Pro')
    application.setOrganizationName('LANBridgePro')
    application.setStyle('Fusion')
    application.setFont(QFont('Segoe UI', 10))
    directory = app_data()
    lock = QLockFile(str(directory / 'application.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, 'LANBridge Pro', 'The app is already running for this Windows user.\nالبرنامج مفتوح بالفعل.')
        return 1
    configure_logging(directory / 'logs')
    try:
        service = ApplicationService(directory)
        window = MainWindow(service)
    except Exception as exc:
        QMessageBox.critical(None, 'LANBridge Pro', str(exc))
        return 1
    application.commitDataRequest.connect(lambda manager: service.engine.stop())
    window.show()
    try:
        return application.exec()
    finally:
        service.discovery.close()
        lock.unlock()


if __name__ == '__main__':
    raise SystemExit(main())
