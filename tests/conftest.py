import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='session', autouse=True)
def qt_application():
    """One Qt application for both native self-tests and UI tests in the same process."""
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
