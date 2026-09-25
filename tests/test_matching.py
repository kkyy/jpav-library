import json
from threading import Event

import pytest
from sqlalchemy import func, select

from av_library.db.models import MovieMatch
from av_library.providers.file_import import FileCatalogProvider
from av_library.scanner.files import ScanOptions
from av_library.services.actresses import ActressInput, ActressService
from av_library.services.matching import MatchService
from av_library.services.metadata_sync import MetadataService, SyncError
from av_library.services.scanning import ScanService
from av_library.ui.metadata_dialog import MetadataDialog


@pytest.fixture
def setup_library(database, tmp_path):
    folder = tmp_path / "videos"
    folder.mkdir()
    actress = ActressService(database).save(ActressInput("翼舞", str(folder)))
    catalog = tmp_path / "catalog.json"
    service = MetadataService(database)
    matches = MatchService(database)
    scan = ScanService(database)

    def sync(rows):
        catalog.write_text(json.dumps(rows), encoding="utf-8")
        provider = FileCatalogProvider(str(catalog), actress.name)
        service.bind(actress.id, provider, provider.search_actresses(actress.name)[0])
        return service.sync(actress.id, provider)

    return folder, actress, catalog, sync, scan, matches


def test_real_catalog_gap_duplicates_manual_ignore_and_latest(setup_library, database):
    folder, actress, _catalog, sync, scan, matches = setup_library
    codes = ["MIDV-001", "MIDV-002", "MIDV-004", "MIDV-005", "MIDV-999"]
    sync(
        [
            {"code": code, "title": code, "release_date": f"2026-09-{day:02d}"}
            for day, code in enumerate(codes, 1)
        ]
    )
    for name in ("MIDV-001.mp4", "MIDV001-4K.mkv", "MIDV_002.mp4", "unknown.mp4", "ABC-777.mp4"):
        (folder / name).touch()
    scan.run(actress.id, ScanOptions(), Event())
    views = matches.views(actress.id)
    summary = matches.summary(actress.id, views)
    assert (summary.total, summary.collected, summary.missing) == (5, 2, 3)
    assert summary.latest.movie.code == "MIDV-999"
    assert summary.latest.status == "missing"
    assert len(next(view for view in views if view.movie.code == "MIDV-001").local_paths) == 2
    assert summary.new_count == 0  # Initial import is a baseline.
    assert len(matches.file_matches(actress.id)) == 3
    unknown = next(row for row in scan.files(actress.id) if row.filename == "unknown.mp4")
    scan.set_manual_code(unknown.id, "MIDV-004")
    assert matches.summary(actress.id).collected == 3
    missing_movie = next(
        view.movie for view in matches.views(actress.id) if view.movie.code == "MIDV-005"
    )
    matches.set_ignored(actress.id, missing_movie.id, True)
    summary = matches.summary(actress.id)
    assert (summary.needed, summary.collected, summary.missing, summary.completion_percent) == (
        4,
        3,
        1,
        75.0,
    )
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MovieMatch)) == 4
    scan.set_manual_code(unknown.id, None)
    assert matches.summary(actress.id).collected == 2


def test_scan_failure_preserves_match_and_successful_removal_clears_it(setup_library):
    folder, actress, _catalog, sync, scan, matches = setup_library
    sync([{"code": "MIDV-001"}])
    video = folder / "MIDV-001.mp4"
    video.touch()
    scan.run(actress.id, ScanOptions(), Event())
    assert matches.summary(actress.id).collected == 1
    video.unlink()
    cancelled = Event()
    cancelled.set()
    assert scan.run(actress.id, ScanOptions(), cancelled).status == "cancelled"
    assert matches.summary(actress.id).collected == 1
    scan.run(actress.id, ScanOptions(), Event())
    assert (matches.summary(actress.id).collected, matches.summary(actress.id).missing) == (0, 1)


def test_sync_after_scan_and_failed_update_preserves_matches(setup_library):
    folder, actress, catalog, sync, scan, matches = setup_library
    (folder / "MIDV-001.mp4").touch()
    scan.run(actress.id, ScanOptions(), Event())
    assert not matches.file_matches(actress.id)
    sync([{"code": "MIDV-001"}])
    assert matches.summary(actress.id).collected == 1
    catalog.write_text(json.dumps([{"code": "invalid"}]), encoding="utf-8")
    provider = FileCatalogProvider(str(catalog), actress.name)
    with pytest.raises(SyncError):
        MetadataService(matches.database).sync(actress.id, provider)
    assert matches.summary(actress.id).collected == 1
    sync([{"code": "MIDV-001"}, {"code": "MIDV-002"}])
    summary = matches.summary(actress.id)
    assert (summary.total, summary.missing, summary.new_count) == (2, 1, 1)


def test_directory_switch_invalidates_old_match(setup_library, tmp_path):
    folder, actress, _catalog, sync, scan, matches = setup_library
    sync([{"code": "MIDV-001"}])
    (folder / "MIDV-001.mp4").touch()
    scan.run(actress.id, ScanOptions(), Event())
    assert matches.summary(actress.id).collected == 1
    new_folder = tmp_path / "new"
    new_folder.mkdir()
    ActressService(matches.database).save(ActressInput("翼舞", str(new_folder)), actress.id)
    assert matches.summary(actress.id).collected == 0
    scan.run(actress.id, ScanOptions(), Event())
    assert not matches.file_matches(actress.id)


def test_metadata_ui_filter_and_ignore(setup_library, qapp):
    folder, actress, _catalog, sync, scan, matches = setup_library
    sync([{"code": "MIDV-001"}, {"code": "MIDV-002"}])
    (folder / "MIDV-001.mp4").touch()
    scan.run(actress.id, ScanOptions(), Event())
    dialog = MetadataDialog(MetadataService(matches.database), actress)
    dialog.filter.setCurrentText("缺少")
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "MIDV-002"
    dialog.table.selectRow(0)
    dialog.toggle_ignored()
    assert dialog.table.rowCount() == 0
    assert "完成度 100.0%" in dialog.collection.text()
    dialog.filter.setCurrentText("已忽略")
    assert dialog.table.rowCount() == 1
    dialog.close()


def test_compilations_are_visible_but_excluded_from_collection_statistics(setup_library):
    _folder, actress, _catalog, sync, _scan, matches = setup_library
    sync(
        [
            {"code": "MIDV-001", "title": "单体作品"},
            {"code": "BEST-001", "title": "女优 BEST 总集编", "is_compilation": True},
            {"code": "MULTI-001", "title": "多人企划", "is_solo": False},
        ]
    )
    views = matches.views(actress.id)
    assert len(views) == 3
    assert next(view for view in views if view.movie.code == "BEST-001").status == "compilation"
    summary = matches.summary(actress.id, views)
    assert (summary.total, summary.compilations, summary.multi_actress, summary.needed, summary.missing) == (
        1,
        1,
        1,
        1,
        1,
    )


def test_shared_work_collected_from_other_actress_folder(database, tmp_path):
    first_folder = tmp_path / "first"
    second_folder = tmp_path / "second"
    first_folder.mkdir()
    second_folder.mkdir()
    actresses = ActressService(database)
    first = actresses.save(ActressInput("女优一", str(first_folder)))
    second = actresses.save(ActressInput("女优二", str(second_folder)))
    metadata = MetadataService(database)
    for actress in (first, second):
        path = tmp_path / f"catalog-{actress.id}.json"
        path.write_text(json.dumps([{"code": "ABC-123"}]), encoding="utf-8")
        provider = FileCatalogProvider(str(path), actress.name)
        metadata.bind(actress.id, provider, provider.search_actresses(actress.name)[0])
        metadata.sync(actress.id, provider)
    (first_folder / "ABC-123.mp4").touch()
    ScanService(database).run(first.id, ScanOptions(), Event())
    assert MatchService(database).summary(second.id).collected == 1
