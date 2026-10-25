from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

logger = logging.getLogger('lanbridge.ui.worker')


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object)
    progress = Signal(object)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function: Callable):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(self.signals.progress.emit)
            self.signals.result.emit(result)
        except Exception as exc:
            # Expected operational messages contain no credential objects.
            logger.error('Background operation failed: %s', exc)
            self.signals.error.emit(exc)
        finally:
            self.signals.finished.emit()
