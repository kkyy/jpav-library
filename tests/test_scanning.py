import builtins
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import select, text

from av_library.db.database import Database
from av_library.db.migrations import MIGRATIONS
from av_library.db.models import Actress, LocalFile
from av_library.scanner import files as scanner
from av_library.scanner.files import ScanOptions, scan_filenames
from av_library.services.actresses import ActressInput, ActressService
from av_library.services.scanning import ScanService


@pytest.fixture
def library(database, tmp_path):
    root = tmp_path / "视频"
    root.mkdir()
    actress = ActressService(database).save(ActressInput("翼舞", str(root)))
    return root, actress, ScanService(database)


def test_only_filenames_no_video_open_or_parent_inference(tmp_path, monkeypatch):
    root = tmp_path / "MIDV-999"
    root.mkdir()
    (root / "unknown.MP4").write_bytes(b"not a video")
    child = root / "子目录"
    child.mkdir()
    (child / "ABC_123.mkv").touch()
    (child / "ABC-124.txt").touch()

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("Scanning must never open video content")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    result = scan_filenames(str(root), ScanOptions(), Event())
    assert len(result.files) == 2
    assert not result.errors
    assert next(f for f in result.files if f.filename == "unknown.MP4").parsed.code is None
    assert {f.parsed.code for f in result.files} == {None, "ABC-123"}


def test_idempotent_scan_manual_survives_and_missing_is_retained(library, database):
    root, actress, service = library
    (root / "MIDV-123.mp4").touch()
    (root / "MIDV-123-4K.mp4").touch()
    unknown = root / "unknown.mp4"
    unknown.touch()
    assert service.run(actress.id, ScanOptions(), Event()).status == "success"
    first = service.files(actress.id)
    manual = next(row for row in first if row.filename == unknown.name)
    service.set_manual_code(manual.id, "midv_125")
    service.run(actress.id, ScanOptions(), Event())
    assert {row.id for row in service.files(actress.id)} == {row.id for row in first}
    assert (
        next(row for row in service.files(actress.id) if row.id == manual.id).effective_code
        == "MIDV-125"
    )
    unknown.unlink()
    service.run(actress.id, ScanOptions(), Event())
    assert len(service.files(actress.id)) == 3
    assert sum(row.is_present for row in service.files(actress.id)) == 2
    with database.sessions() as session:
        assert session.get(Actress, actress.id).last_scanned_at is not None
    unknown.touch()
    service.run(actress.id, ScanOptions(), Event())
    restored = next(row for row in service.files(actress.id) if row.id == manual.id)
    assert restored.is_present and restored.manual_code == "MIDV-125"
    service.set_manual_code(manual.id, None)
    assert (
        next(row for row in service.files(actress.id) if row.id == manual.id).effective_code is None
    )


def test_permission_error_and_cancel_preserve_previous_snapshot(library, monkeypatch):
    root, actress, service = library
    child = root / "nested"
    child.mkdir()
    (child / "ABC-001.mp4").touch()
    service.run(actress.id, ScanOptions(), Event())
    previous = service.files(actress.id)
    (root / "ABC-002.mp4").touch()
    real_scandir = scanner.os.scandir

    def denied(path):
        if Path(path) == child:
            raise PermissionError("test denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", denied)
    result = service.run(actress.id, ScanOptions(), Event())
    assert result.status == "partial"
    assert "test denied" in result.error_summary
    assert [row.id for row in service.files(actress.id)] == [row.id for row in previous]
    assert service.files(actress.id)[0].is_present
    cancel = Event()
    result = service.run(actress.id, ScanOptions(), cancel, lambda _: cancel.set())
    assert result.status == "cancelled"
    assert service.files(actress.id)[0].is_present


def test_narrow_scope_does_not_invalidate_other_extensions_or_subdirs(library):
    root, actress, service = library
    nested = root / "nested"
    nested.mkdir()
    paths = [root / "ABC-001.mp4", root / "ABC-002.mkv", nested / "ABC-003.mp4"]
    for path in paths:
        path.touch()
    service.run(actress.id, ScanOptions(), Event())
    for path in paths:
        path.unlink()
    service.run(actress.id, ScanOptions(False, (".mp4",)), Event())
    current = {row.filename: row.is_present for row in service.files(actress.id)}
    assert current == {"ABC-001.mp4": False, "ABC-002.mkv": True, "ABC-003.mp4": True}
    service.run(actress.id, ScanOptions(), Event())
    assert not any(row.is_present for row in service.files(actress.id))


def test_offline_changed_root_and_successful_switch(library, database, tmp_path):
    root, actress, service = library
    (root / "ABC-001.mp4").touch()
    service.run(actress.id, ScanOptions(), Event())
    target = tmp_path / "new-root"
    actresses = ActressService(database)
    actresses.save(ActressInput(actress.name, str(target)), actress.id)
    assert actresses.get(actress.id).last_scanned_at is None
    assert service.run(actress.id, ScanOptions(), Event()).status == "failed"
    assert service.files(actress.id)[0].is_present
    target.mkdir()
    (target / "ABC-002.mp4").touch()
    assert service.run(actress.id, ScanOptions(), Event()).status == "success"
    assert {row.filename for row in service.files(actress.id) if row.is_present} == {"ABC-002.mp4"}
    actresses.delete(actress.id)
    assert service.files(actress.id) == []
    assert (root / "ABC-001.mp4").exists() and (target / "ABC-002.mp4").exists()


def test_setting_validation_and_persistence(library):
    _, _, service = library
    assert service.save_options(False, "MP4, .MKV, mp4").extensions == (".mp4", ".mkv")
    assert service.options() == ScanOptions(False, (".mp4", ".mkv"))
    with pytest.raises(ValueError):
        service.save_options(True, "*.*")


def test_v1_database_upgrade_preserves_profiles(tmp_path):
    db = Database(tmp_path / "old.sqlite3")
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)"))
            for statement in MIGRATIONS[0][1]:
                connection.execute(text(statement))
            connection.execute(text("INSERT INTO schema_migrations(version) VALUES (1)"))
        record = ActressService(db).save(ActressInput("旧资料", str(tmp_path / "offline")))
        db.initialize()
        assert ActressService(db).get(record.id).name == "旧资料"
        with db.sessions() as session:
            assert list(session.scalars(select(LocalFile))) == []
    finally:
        db.close()


def test_cancel_during_commit_rolls_back_file_invalidations(library, monkeypatch):
    from av_library.services import scanning as service_module

    root, actress, service = library
    (root / "ABC-001.mp4").touch()
    service.run(actress.id, ScanOptions(), Event())
    previous = service.files(actress.id)[0]
    (root / "ABC-002.mp4").touch()
    cancel = Event()
    snapshot = scan_filenames(str(root), ScanOptions(), Event())

    class CancelAfterFirstFile(list):
        def __iter__(self):
            for index, item in enumerate(super().__iter__()):
                if index == 1:
                    cancel.set()
                yield item

    snapshot.files = CancelAfterFirstFile(snapshot.files)
    monkeypatch.setattr(service_module, "scan_filenames", lambda *_args: snapshot)
    assert service.run(actress.id, ScanOptions(), cancel).status == "cancelled"
    rows = service.files(actress.id)
    assert len(rows) == 1 and rows[0].id == previous.id and rows[0].is_present


def test_directory_links_are_not_traversed(tmp_path, monkeypatch):
    import os
    import stat
    from contextlib import contextmanager
    from types import SimpleNamespace

    class LinkEntry:
        path = str(tmp_path / "loop")
        name = "loop"

        def is_symlink(self):
            return os.name != "nt"

        def is_dir(self, **_kwargs):
            return True

        def stat(self, **_kwargs):
            return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)

    calls = []

    @contextmanager
    def fake_scandir(path):
        calls.append(path)
        assert Path(path) == tmp_path, "Must not follow a directory link"
        yield iter([LinkEntry()])

    monkeypatch.setattr(scanner.os, "scandir", fake_scandir)
    result = scan_filenames(str(tmp_path), ScanOptions(), Event())
    assert not result.files and result.skipped_links == 1
    assert len(calls) == 1
