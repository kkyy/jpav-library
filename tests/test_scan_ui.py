from PySide6.QtCore import QEventLoop, QTimer

from av_library.services.actresses import ActressInput, ActressService
from av_library.services.scanning import ScanService
from av_library.ui.scan_dialog import ScanDialog


def finish_worker(dialog):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    dialog.worker.finished.connect(loop.quit)
    timer.start(10000)
    loop.exec()
    timer.stop()
    assert dialog.worker is None


def test_background_scan_filters_and_manual_correction(database, tmp_path, qapp):
    app = qapp
    root = tmp_path / "video"
    root.mkdir()
    for filename in ("MIDV-123.mp4", "MIDV-123-4K.mp4", "unknown.mp4", "MIDV-124 MIDV-125.mkv"):
        (root / filename).touch()
    actress = ActressService(database).save(ActressInput("翼舞", str(root)))
    dialog = ScanDialog(ScanService(database), actress)
    dialog.show()
    app.processEvents()
    dialog.start_scan()
    assert not dialog.start_button.isEnabled()
    finish_worker(dialog)
    assert dialog.start_button.isEnabled()
    assert dialog.table.rowCount() == 4
    dialog.filter.setCurrentIndex(3)
    assert dialog.table.rowCount() == 2
    dialog.filter.setCurrentIndex(2)
    assert dialog.table.rowCount() == 2
    unknown = next(row for row in dialog.rows if row.filename == "unknown.mp4")
    dialog.apply_manual(unknown.id, "abc123")
    assert dialog.table.rowCount() == 1
    dialog.apply_manual(unknown.id, None)
    assert dialog.table.rowCount() == 2
    dialog.search.setText("unknown")
    assert dialog.table.rowCount() == 1
    dialog.close()


def test_close_requests_cancel_and_waits_for_worker(database, tmp_path, monkeypatch, qapp):
    app = qapp
    actress = ActressService(database).save(ActressInput("翼舞", str(tmp_path)))
    service = ScanService(database)
    original_run = service.run

    def run_until_cancelled(actress_id, options, cancel, progress):
        assert cancel.wait(5)
        return original_run(actress_id, options, cancel, progress)

    monkeypatch.setattr(service, "run", run_until_cancelled)
    dialog = ScanDialog(service, actress)
    dialog.show()
    app.processEvents()
    dialog.start_scan()
    dialog.reject()
    assert dialog.worker is not None
    assert dialog.worker.cancel.is_set()
    finish_worker(dialog)
    assert not dialog.isVisible()
    assert service.latest(actress.id).status == "cancelled"


def test_changed_root_does_not_show_old_records_as_current(database, tmp_path, qapp):
    from threading import Event

    from av_library.scanner.files import ScanOptions

    app = qapp
    (tmp_path / "ABC-123.mp4").touch()
    actresses = ActressService(database)
    actress = actresses.save(ActressInput("翼舞", str(tmp_path)))
    service = ScanService(database)
    service.run(actress.id, ScanOptions(), Event())
    actress = actresses.save(ActressInput("翼舞", str(tmp_path / "elsewhere")), actress.id)
    dialog = ScanDialog(service, actress)
    assert dialog.table.rowCount() == 0
    dialog.filter.setCurrentIndex(4)
    assert dialog.table.rowCount() == 1
    dialog.close()
    app.processEvents()
