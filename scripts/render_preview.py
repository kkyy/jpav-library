"""Render the actual Qt window with temporary sample profiles, never personal data."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from av_library.config import AppPaths
from av_library.db.database import Database
from av_library.providers.file_import import FileCatalogProvider
from av_library.scanner.files import ScanOptions
from av_library.services.actresses import ActressInput, ActressService
from av_library.services.metadata_sync import MetadataService
from av_library.services.scanning import ScanService
from av_library.ui.actress_dialog import ActressDialog
from av_library.ui.main_window import MainWindow
from av_library.ui.metadata_dialog import MetadataDialog
from av_library.ui.scan_dialog import ScanDialog
from av_library.ui.theme import STYLESHEET


def main():
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(exist_ok=True)
    app = QApplication([])
    # The offscreen platform does not enumerate Windows system fonts itself.
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        if (fonts / filename).exists():
            QFontDatabase.addApplicationFont(str(fonts / filename))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    with TemporaryDirectory() as temporary:
        paths = AppPaths(Path(temporary))
        paths.prepare()
        database = Database(paths.database)
        database.initialize()
        service = ActressService(database)
        for name in ("翼舞", "河北彩花", "石川澪"):
            service.save(ActressInput(name, str(Path("D:/AV") / name)))
        window = MainWindow(service, paths)
        window.show()
        app.processEvents()
        window.grab().save(str(output / "phase2-management.png"))
        dialog = ActressDialog(service, parent=window)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(output / "phase2-add.png"))
        dialog.close()
        sample_root = Path(temporary) / "示例视频"
        sample_root.mkdir()
        for filename in (
            "MIDV-123.mp4",
            "MIDV123-4K.mp4",
            "ABC_001.mkv",
            "unknown.mp4",
            "MIDV-124 MIDV-125.mp4",
        ):
            (sample_root / filename).touch()
        sample = service.list("翼舞")[0]
        sample = service.save(ActressInput("翼舞", str(sample_root)), sample.id)
        scans = ScanService(database)
        scans.run(sample.id, ScanOptions(), Event())
        scan_dialog = ScanDialog(scans, sample, window)
        scan_dialog.show()
        app.processEvents()
        scan_dialog.grab().save(str(output / "phase2-scan.png"))
        scan_dialog.close()
        sample_catalog = Path(temporary) / "sample_catalog.json"
        sample_catalog.write_text(
            json.dumps(
                {
                    "movies": [
                        {
                            "code": "MIDV-123",
                            "title": "示例作品 A",
                            "release_date": "2026-09-01",
                            "manufacturer": "示例厂商",
                        },
                        {"code": "MIDV-124", "title": "示例作品 B", "release_date": "2026-09-10"},
                        {"code": "ABC-001", "title": "示例作品 C", "release_date": "2026-08-15"},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        metadata = MetadataService(database)
        provider = FileCatalogProvider(str(sample_catalog), sample.name)
        metadata.bind(sample.id, provider, provider.search_actresses(sample.name)[0])
        metadata.sync(sample.id, provider)
        metadata_dialog = MetadataDialog(metadata, sample, window)
        metadata_dialog.show()
        app.processEvents()
        metadata_dialog.grab().save(str(output / "phase3-metadata.png"))
        metadata_dialog.close()
        window.close()
        database.close()
    print(output)


if __name__ == "__main__":
    main()
