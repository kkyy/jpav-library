import json

from PySide6.QtCore import QEventLoop, QTimer

from av_library.services.actresses import ActressInput, ActressService
from av_library.services.discovery import DiscoveryReport, SourceDiscovery
from av_library.services.metadata_sync import MetadataService
from av_library.ui.metadata_dialog import MetadataDialog


def wait_for_task(dialog):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    dialog.worker.finished.connect(loop.quit)
    timer.start(10000)
    loop.exec()
    assert timer.isActive(), "Metadata worker timed out"
    timer.stop()
    assert dialog.worker is None


def test_json_import_from_dialog_shows_movies_and_reimport_is_idempotent(database, tmp_path, qapp):
    actress = ActressService(database).save(ActressInput("翼舞", str(tmp_path / "videos")))
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "movies": [
                    {"code": "MIDV-123", "title": "新作品", "release_date": "2026-09-01"},
                    {"code": "MIDV-124", "title": "另一部", "release_date": "2026-09-02"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = MetadataService(database)
    dialog = MetadataDialog(service, actress)
    dialog.show()
    qapp.processEvents()
    dialog.import_file(str(path))
    wait_for_task(dialog)
    assert dialog.table.rowCount() == 2
    assert "已收录 2 部" in dialog.status.text()
    assert "本地导入" in dialog.sources.text()
    assert dialog.table.item(0, 0).text() == "MIDV-124"
    dialog.import_file(str(path))
    wait_for_task(dialog)
    assert dialog.table.rowCount() == 2
    assert service.latest(actress.id).added_count == 0
    dialog.search.setText("MIDV-123")
    assert dialog.table.rowCount() == 1
    dialog.close()


def test_invalid_import_does_not_bind_source(database, tmp_path, qapp):
    actress = ActressService(database).save(ActressInput("翼舞", str(tmp_path / "videos")))
    path = tmp_path / "broken.json"
    path.write_text('{"movies":[{"code":"bad"}]}', encoding="utf-8")
    service = MetadataService(database)
    dialog = MetadataDialog(service, actress)
    errors = []
    dialog.show_error = errors.append
    dialog.import_file(str(path))
    wait_for_task(dialog)
    assert errors and service.bindings(actress.id) == []
    assert service.movies(actress.id) == []
    dialog.close()


def test_auto_discovery_button_reports_source_scope(database, tmp_path, qapp, monkeypatch):
    actress = ActressService(database).save(
        ActressInput("翼舞", str(tmp_path / "videos"), japanese_name="つばさ舞")
    )
    seen = []

    def discover(_self, selected, _cancel, _progress):
        seen.append(selected.japanese_name)
        return DiscoveryReport((SourceDiscovery("S1 官网", "success", "读取 105 条"),))

    monkeypatch.setattr("av_library.ui.metadata_dialog.SoloCatalogDiscoveryService.run", discover)
    dialog = MetadataDialog(MetadataService(database), actress)
    dialog.discovery_button.click()
    wait_for_task(dialog)
    assert seen == ["つばさ舞"]
    assert "S1 官网：读取 105 条" in dialog.status.text()
    dialog.close()
