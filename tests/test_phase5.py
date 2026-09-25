import io
import json
from threading import Event

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from av_library.providers.file_import import FileCatalogProvider
from av_library.scanner.files import ScanOptions
from av_library.services.actresses import ActressInput, ActressService
from av_library.services.covers import CoverError, CoverService
from av_library.services.metadata_sync import MetadataService
from av_library.services.scanning import ScanService
from av_library.services.search import SearchService
from av_library.ui.search_dialog import SearchDialog


def test_global_search_finds_actress_code_title_and_local_path(database, tmp_path, qapp):
    folder = tmp_path / "videos"
    folder.mkdir()
    actress = ActressService(database).save(ActressInput("翼舞", str(folder), aliases=("舞舞",)))
    catalog = tmp_path / "works.json"
    catalog.write_text(json.dumps([{"code": "MIDV-123", "title": "测试作品"}]), encoding="utf-8")
    provider = FileCatalogProvider(str(catalog), actress.name)
    metadata = MetadataService(database)
    metadata.bind(actress.id, provider, provider.search_actresses(actress.name)[0])
    metadata.sync(actress.id, provider)
    video = folder / "MIDV123.mp4"
    video.touch()
    ScanService(database).run(actress.id, ScanOptions(), Event())
    search = SearchService(database)
    assert search.search("舞舞").actresses[0].id == actress.id
    assert search.search("MIDV-123").movies[0].local_paths == (str(video),)
    assert search.search("midv123").movies[0].code == "MIDV-123"
    assert search.search("测试作品").movies[0].status == "collected"
    assert search.search("%").movies == ()
    dialog = SearchDialog(search)
    dialog.query.setText("MIDV-123")
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 4).text() == str(video)
    dialog.close()


def test_cover_cache_https_bounded_and_reused(tmp_path, monkeypatch, qapp):
    image = QImage(10, 20, QImage.Format.Format_RGB32)
    image.fill("red")
    bytes_buffer = QByteArray()
    buffer = QBuffer(bytes_buffer)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")

    class Headers:
        def get_content_type(self):
            return "image/png"

    class Response(io.BytesIO):
        headers = Headers()

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response(bytes(bytes_buffer))

    monkeypatch.setattr("av_library.services.covers.build_opener", lambda *_args: Opener())
    covers = CoverService(tmp_path / "covers")
    url = "https://example.com/cover.png"
    path = covers.fetch(url)
    assert path.is_file() and covers.cached(url) == path
    assert covers.fetch(url) == path and len(calls) == 1
    with pytest.raises(CoverError):
        covers.fetch("http://example.com/cover.png")
    with pytest.raises(CoverError):
        covers.fetch("https://127.0.0.1/cover.png")
