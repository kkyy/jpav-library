import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from av_library.db.database import Database


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.processEvents()


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.initialize()
    yield db
    db.close()
