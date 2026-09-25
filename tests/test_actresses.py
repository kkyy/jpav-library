from dataclasses import replace

import pytest
from sqlalchemy import text

from av_library.db.database import Database
from av_library.services.actresses import ActressInput, ActressService, ValidationError


def test_crud_survives_restart_and_never_changes_local_files(database, tmp_path):
    folder = tmp_path / "翼舞"
    folder.mkdir()
    video = folder / "MIDV-123.mp4"
    video.write_bytes(b"test fixture, not a real video")
    service = ActressService(database)
    row = service.save(ActressInput("翼舞", str(folder), "日文姓名", ("别名", "别名")))
    other = Database(tmp_path / "test.sqlite3")
    try:
        other.initialize()
        reopened = ActressService(other).get(row.id)
        assert reopened.aliases == ["别名"]
        assert reopened.japanese_name == "日文姓名"
    finally:
        other.close()
    updated = service.save(ActressInput("翼舞", str(tmp_path / "离线目录")), row.id)
    assert updated.created_at == row.created_at
    assert updated.last_synced_at is None
    service.delete(row.id)
    assert service.list() == []
    assert video.read_bytes() == b"test fixture, not a real video"


def test_duplicate_name_or_folder_rolls_back(database, tmp_path):
    service = ActressService(database)
    data = ActressInput("ＡＢＣ", str(tmp_path / "first"))
    service.save(data)
    with pytest.raises(ValidationError):
        service.save(replace(data, name="abc", folder_path=str(tmp_path / "second")))
    with pytest.raises(ValidationError):
        service.save(replace(data, name="Another"))
    assert len(service.list()) == 1
    service.save(ActressInput("第二位", str(tmp_path / "second")))
    assert len(service.list()) == 2


def test_conflicting_edit_preserves_original(database, tmp_path):
    service = ActressService(database)
    first = service.save(ActressInput("第一位", str(tmp_path / "a")))
    second = service.save(ActressInput("第二位", str(tmp_path / "b")))
    with pytest.raises(ValidationError):
        service.save(ActressInput(first.name, second.folder_path), second.id)
    assert service.get(second.id).name == "第二位"


@pytest.mark.parametrize("name,folder", [(" ", "valid"), ("A", ""), ("A", "relative")])
def test_invalid_input(database, tmp_path, name, folder):
    path = str(tmp_path / "valid") if folder == "valid" else folder
    with pytest.raises(ValidationError):
        ActressService(database).save(ActressInput(name, path))


def test_search_names_and_aliases_literal(database, tmp_path):
    service = ActressService(database)
    service.save(ActressInput("翼舞", str(tmp_path / "a"), "テスト", ("ＭＡＩ",)))
    assert len(service.list("mai")) == 1
    assert len(service.list("テスト")) == 1
    assert service.list("%") == []


def test_migrations_idempotent_and_foreign_keys_enabled(database):
    database.initialize()
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM schema_migrations")) == 6
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("SELECT count(*) FROM settings")) == 3


def test_newer_database_rejected_without_reset(database):
    with database.engine.begin() as connection:
        connection.execute(text("INSERT INTO schema_migrations(version) VALUES (99)"))
    with pytest.raises(RuntimeError, match="版本"):
        database.initialize()
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT max(version) FROM schema_migrations")) == 99


def test_ddl_rollback(database):
    with pytest.raises(RuntimeError), database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE should_rollback(id INTEGER)"))
        raise RuntimeError("simulated migration failure")
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM sqlite_master WHERE name='should_rollback'")
            )
            == 0
        )
