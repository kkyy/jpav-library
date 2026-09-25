import argparse
import logging
import sys

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from av_library.config import AppPaths
from av_library.db.database import Database
from av_library.services.actresses import ActressService
from av_library.services.logging_setup import configure_logging
from av_library.ui.main_window import MainWindow
from av_library.ui.theme import STYLESHEET


def main() -> int:
    parser = argparse.ArgumentParser(description="本地作品库 · JPAV Library")
    parser.add_argument("--data-dir", help="数据库和缓存目录（默认保存在本机用户目录）")
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("JPAV Library")
    app.setOrganizationName("LocalLibrary")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    paths = AppPaths.resolve(args.data_dir)
    database = None
    lock = None
    try:
        paths.prepare()
        configure_logging(paths.logs)
        lock = QLockFile(str(paths.data_dir / "library.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            raise RuntimeError("此数据目录已在使用中，或无法创建锁文件。请检查已有窗口和目录权限。")
        database = Database(paths.database)
        database.initialize()
        logging.getLogger("av_library.app").info("Application started")
        window = MainWindow(ActressService(database), paths)
        window.show()
        return app.exec()
    except (OSError, RuntimeError, SQLAlchemyError) as error:
        logging.getLogger("av_library.app").error("Startup failed: %s", type(error).__name__)
        box = QMessageBox()
        box.setWindowTitle("启动失败")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"无法启动本地作品库。\n\n{error}")
        box.exec()
        return 1
    finally:
        if database:
            database.close()
        if lock and lock.isLocked():
            lock.unlock()
